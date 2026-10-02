"""Outbound ports: what the service needs from the outside world. Adapters live in duckless.adapters."""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from duckless.core.job import JobSpec, JobStatus, LogLine
from duckless.core.quota import Quota


class Executor(Protocol):
    """Runs a job on some compute: Cloud Batch today, Cloud Run Jobs / GKE later."""

    def submit(self, spec: JobSpec) -> JobStatus: ...

    def get(self, job_id: str) -> JobStatus:
        """Raises JobNotFoundError."""
        ...

    def cancel(self, job_id: str) -> None:
        """Raises JobNotFoundError."""
        ...


class ArtifactStore(Protocol):
    """Per-job files in the work bucket: the submitted source, the runner's metrics."""

    def upload_source(self, job_id: str, source: Path) -> str:
        """Returns the URI the runner reads the source from."""
        ...

    def runner_env(self, job_id: str) -> Mapping[str, str]:
        """Env the runner needs to find the work bucket and write its metrics."""
        ...

    def read_metrics(self, job_id: str) -> Mapping[str, Any] | None:
        """None until the runner has written them."""
        ...


class LogReader(Protocol):
    def read(self, job_uid: str, since: datetime | None = None, limit: int = 200) -> tuple[LogLine, ...]:
        """Runner log lines of a job, oldest first."""
        ...


class QuotaReader(Protocol):
    def regional_quotas(self, region: str) -> Mapping[str, Quota]: ...
