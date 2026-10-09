"""Operations shared by the CLI, the SDK and (later) the SaaS control plane.

Each function takes the ports it needs as keyword arguments; duckless.wiring binds them to
the GCP adapters. Rules stay in duckless.core, side effects go through the ports.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from duckless.core.errors import DucklessError
from duckless.core.infra import (
    BOOTSTRAP_APIS,
    CATALOG_BOOTSTRAP_APIS,
    InfraRequest,
    InfraStatus,
    deployment_id,
    deployment_inputs,
    infra_sa_roles,
    infra_service_account_id,
    module_prefix,
    psa_cleanup_command,
    psa_range_name,
    resolved_request,
    runner_service_account,
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
    platform_env: Mapping[str, str] | None = None,
) -> JobStatus:
    # Everything that can be rejected is rejected before the upload.
    draft = plan_job(request, default_image=default_image, now=clock(), nonce=nonce())
    source_uri = artifact_store.upload_source(draft.job_id, request.source) if request.source else None
    env = merged_env(request.env, {**(platform_env or {}), **artifact_store.runner_env(draft.job_id)})
    return executor.submit(replace(draft, source_uri=source_uri, env=env))


def get_job(job_id: str, *, executor: Executor, artifact_store: ArtifactStore) -> JobReport:
    status = executor.get(job_id)
    return JobReport(status, artifact_store.read_metrics(job_id) if status.state.is_terminal else None)


def job_logs(
    job_id: str, *, executor: Executor, log_reader: LogReader, since: datetime | None = None, limit: int = 200
) -> tuple[LogLine, ...]:
    # Log entries are labelled by platform-specific ids (Batch job uid, Cloud Run execution).
    return log_reader.read(executor.get(job_id), since=since, limit=limit)


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

    current = deployer.get(request.project, request.region, deployment_id(request))
    request = resolved_request(request, current)
    if request.ducklake:
        bootstrap.enable_apis(request.project, CATALOG_BOOTSTRAP_APIS)
        if bootstrap.has_private_service_access(request.project, request.network):
            on_step(f"DuckLake catalog: reusing the private services access of network '{request.network}'")
        else:
            on_step(f"DuckLake catalog: setting up private services access on network '{request.network}'")
            bootstrap.create_private_service_access(request.project, request.network, psa_range_name(request))

    on_step("service account for Infra Manager")
    infra_sa = bootstrap.ensure_service_account(
        request.project, infra_service_account_id(request), "DuckLess infra (Infrastructure Manager)"
    )
    roles = infra_sa_roles(request.ducklake, bool(request.bigquery_datasets))
    if bootstrap.grant_project_roles(request.project, f"serviceAccount:{infra_sa}", roles):
        on_step("waiting for the new IAM grants to propagate")
        wait_for_iam()

    on_step(f"uploading the Terraform module {version}")
    bucket = staging_bucket(request)
    bootstrap.ensure_bucket(request.project, request.region, bucket)
    source = bootstrap.upload_directory(module_dir, bucket, module_prefix(version))

    on_step(
        "applying with Infrastructure Manager (a few minutes"
        + (", ~10 more for Cloud SQL)" if request.ducklake else ")")
    )
    return deployer.apply(
        request.project,
        request.region,
        deployment_id(request),
        source,
        deployment_inputs(request),
        infra_sa,
    )


def destroy_infra(
    request: InfraRequest, *, bootstrap: InfraBootstrap, deployer: InfraDeployer, on_step: Callable[[str], None]
) -> InfraStatus:
    """Deletes the Cloud Run jobs of past runs, the deployment, then what `init` created outside
    Terraform (infra SA, its grants, staging)."""
    on_step("deleting the Cloud Run jobs of past runs")
    deleted = bootstrap.delete_cloud_run_jobs(request.project, request.region, runner_service_account(request))
    on_step(f"  {deleted} deleted")
    current = deployer.get(request.project, request.region, deployment_id(request))
    on_step("deleting the Infra Manager deployment (a few minutes)")
    status = deployer.destroy(request.project, request.region, deployment_id(request))
    if not status.ok:
        return status  # keep the infra SA: it is needed to retry the deletion
    if current and current.outputs.get("ducklake_instance"):
        network = current.outputs.get("network", "default")
        on_step(f"kept: the private services access of network '{network}', shared by every Cloud SQL instance on it")
        on_step(
            "  only if nothing else on that network uses it (other Cloud SQL, Memorystore...), remove it with: "
            + psa_cleanup_command(request.project, network)
        )

    on_step("removing the Infra Manager service account and the staging bucket")
    infra_sa = f"{infra_service_account_id(request)}@{request.project}.iam.gserviceaccount.com"
    bootstrap.revoke_project_roles(
        request.project, f"serviceAccount:{infra_sa}", infra_sa_roles(ducklake=True, bigquery=True)
    )
    bootstrap.delete_service_account(request.project, infra_sa)
    bootstrap.delete_bucket(staging_bucket(request))
    return status
