"""Operations shared by the CLI, the SDK and (later) the SaaS control plane.

Each function takes the ports it needs as keyword arguments; duckless.wiring binds them to
the GCP adapters. Rules stay in duckless.core, side effects go through the ports.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from duckless.core.errors import DucklessError
from duckless.core.job import JobReport, JobRequest, JobStatus, LogLine, merged_env, plan_job
from duckless.core.machine import resolve_machine
from duckless.core.preflight import PreflightReport, preflight_report, rejected_machine
from duckless.ports import ArtifactStore, Executor, LogReader, QuotaReader


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
