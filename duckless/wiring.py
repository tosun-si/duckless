"""Single wiring point: binds the service functions to the GCP adapters.

Adapters are built lazily (and once): `duckless --help` or a rejected job must not pay
for, or fail on, Google credential discovery.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from duckless import service
from duckless.core.job import JobReport, JobRequest, JobStatus, LogLine
from duckless.core.preflight import PreflightReport
from duckless.settings import Settings


@dataclass(frozen=True, slots=True)
class Services:
    run_job: Callable[[JobRequest], JobStatus]
    get_job: Callable[[str], JobReport]
    job_logs: Callable[[str, datetime | None], tuple[LogLine, ...]]
    cancel_job: Callable[[str], None]
    preflight: Callable[[str, bool, int | None], PreflightReport]


def gcp_services(settings: Settings) -> Services:
    @cache
    def executor():
        from google.cloud import batch_v1

        from duckless.adapters.batch import BatchExecutor

        return BatchExecutor(batch_v1.BatchServiceClient(), settings)

    @cache
    def artifact_store():
        from google.cloud import storage

        from duckless.adapters.gcs import GcsArtifactStore

        return GcsArtifactStore(storage.Client(project=settings.project), settings.bucket)

    @cache
    def log_reader():
        from google.cloud import logging as cloud_logging

        from duckless.adapters.cloud_logging import CloudLoggingLogReader

        return CloudLoggingLogReader(cloud_logging.Client(project=settings.project), settings.project)

    @cache
    def quota_reader():
        from google.cloud import compute_v1

        from duckless.adapters.compute_quotas import ComputeQuotaReader

        return ComputeQuotaReader(compute_v1.RegionsClient(), settings.project)

    return Services(
        run_job=lambda request: service.run_job(
            request,
            executor=executor(),
            artifact_store=artifact_store(),
            default_image=settings.image,
            clock=lambda: datetime.now(UTC),
            nonce=lambda: uuid.uuid4().hex,
        ),
        get_job=lambda job_id: service.get_job(job_id, executor=executor(), artifact_store=artifact_store()),
        job_logs=lambda job_id, since: service.job_logs(
            job_id, executor=executor(), log_reader=log_reader(), since=since
        ),
        cancel_job=lambda job_id: service.cancel_job(job_id, executor=executor()),
        preflight=lambda machine, spot, local_ssd_count: service.preflight(
            machine, spot=spot, local_ssd_count=local_ssd_count, quota_reader=quota_reader(), region=settings.region
        ),
    )
