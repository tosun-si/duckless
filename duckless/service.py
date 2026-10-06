"""Operations shared by the CLI, the SDK and (later) the SaaS control plane.

Each function takes the ports it needs as keyword arguments; duckless.wiring binds them to
the GCP adapters. Rules stay in duckless.core, side effects go through the ports.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from duckless.core.errors import DucklessError
from duckless.core.infra import (
    BOOTSTRAP_APIS,
    INFRA_SA_ROLES,
    InfraRequest,
    InfraStatus,
    deployment_id,
    deployment_inputs,
    infra_service_account_id,
    module_prefix,
    staging_bucket,
)
from duckless.core.job import JobReport, JobRequest, JobStatus, LogLine, merged_env, plan_job
from duckless.core.machine import resolve_machine
from duckless.core.org_policy import CONSTRAINTS, org_policy_checks
from duckless.core.preflight import PreflightReport, preflight_report, rejected_machine
from duckless.ports import ArtifactStore, Executor, InfraBootstrap, InfraDeployer, LogReader, QuotaReader


def run_job(
    request: JobRequest,
    *,
    executor: Executor,
    artifact_store: ArtifactStore,
    default_image: str,
    clock: Callable[[], datetime],
    nonce: Callable[[], str],
) -> JobStatus:
    # Everything that can be rejected is rejected before the upload.
    draft = plan_job(request, default_image=default_image, now=clock(), nonce=nonce())
    source_uri = artifact_store.upload_source(draft.job_id, request.source) if request.source else None
    env = merged_env(request.env, artifact_store.runner_env(draft.job_id))
    return executor.submit(replace(draft, source_uri=source_uri, env=env))


def get_job(job_id: str, *, executor: Executor, artifact_store: ArtifactStore) -> JobReport:
    status = executor.get(job_id)
    return JobReport(status, artifact_store.read_metrics(job_id) if status.state.is_terminal else None)


def job_logs(
    job_id: str, *, executor: Executor, log_reader: LogReader, since: datetime | None = None, limit: int = 200
) -> tuple[LogLine, ...]:
    # Batch labels log entries with the job uid, not its id.
    return log_reader.read(executor.get(job_id).uid, since=since, limit=limit)


def cancel_job(job_id: str, *, executor: Executor) -> None:
    executor.cancel(job_id)


def preflight(
    machine: str, *, spot: bool, local_ssd_count: int | None, quota_reader: QuotaReader, region: str
) -> PreflightReport:
    try:
        resolved, count = resolve_machine(machine, local_ssd_count)
    except DucklessError as e:
        return rejected_machine(region, str(e))
    return preflight_report(region, resolved, count, spot, quota_reader.regional_quotas(region))


def init_infra(
    request: InfraRequest,
    *,
    bootstrap: InfraBootstrap,
    deployer: InfraDeployer,
    module_dir: Path,
    version: str,
    on_step: Callable[[str], None],
    wait_for_iam: Callable[[], None],
) -> InfraStatus:
    """Idempotent: a second run applies the module of the current CLI version (upgrade)."""
    on_step("enabling the APIs Infra Manager needs")
    bootstrap.enable_apis(request.project, BOOTSTRAP_APIS)

    on_step("checking organization policies")
    policies = {c: bootstrap.effective_org_policy(request.project, c) for c in CONSTRAINTS}
    checks = org_policy_checks(request.region, policies)
    for check in checks:
        on_step(f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    blocking = [c for c in checks if not c.ok]
    if blocking:
        # Nothing created yet: stop before Infra Manager fails on it minutes later.
        return InfraStatus(
            deployment_id(request), "BLOCKED", error="\n".join(f"{c.name}: {c.detail}" for c in blocking)
        )

    on_step("service account for Infra Manager")
    infra_sa = bootstrap.ensure_service_account(
        request.project, infra_service_account_id(request), "DuckLess infra (Infrastructure Manager)"
    )
    if bootstrap.grant_project_roles(request.project, f"serviceAccount:{infra_sa}", INFRA_SA_ROLES):
        on_step("waiting for the new IAM grants to propagate")
        wait_for_iam()

    on_step(f"uploading the Terraform module {version}")
    bucket = staging_bucket(request)
    bootstrap.ensure_bucket(request.project, request.region, bucket)
    source = bootstrap.upload_directory(module_dir, bucket, module_prefix(version))

    on_step("applying with Infrastructure Manager (a few minutes)")
    return deployer.apply(
        request.project, request.region, deployment_id(request), source, deployment_inputs(request), infra_sa
    )


def destroy_infra(
    request: InfraRequest, *, bootstrap: InfraBootstrap, deployer: InfraDeployer, on_step: Callable[[str], None]
) -> InfraStatus:
    """Deletes the deployment, then what `init` created outside Terraform (infra SA, its grants, staging)."""
    on_step("deleting the Infra Manager deployment (a few minutes)")
    status = deployer.destroy(request.project, request.region, deployment_id(request))
    if not status.ok:
        return status  # keep the infra SA: it is needed to retry the deletion

    on_step("removing the Infra Manager service account and the staging bucket")
    infra_sa = f"{infra_service_account_id(request)}@{request.project}.iam.gserviceaccount.com"
    bootstrap.revoke_project_roles(request.project, f"serviceAccount:{infra_sa}", INFRA_SA_ROLES)
    bootstrap.delete_service_account(request.project, infra_sa)
    bootstrap.delete_bucket(staging_bucket(request))
    return status
