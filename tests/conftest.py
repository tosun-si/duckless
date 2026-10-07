"""In-memory fakes of duckless.ports: structural (Protocol) matches, no inheritance needed."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from duckless.core.errors import JobNotFoundError
from duckless.core.job import JobSpec, JobState, JobStatus, LogLine
from duckless.core.quota import Quota

NOW = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)


class FakeExecutor:
    def __init__(self) -> None:
        self.submitted: dict[str, JobSpec] = {}
        self.states: dict[str, JobState] = {}
        self.cancelled: list[str] = []

    def submit(self, spec: JobSpec) -> JobStatus:
        self.submitted[spec.job_id] = spec
        self.states[spec.job_id] = JobState.QUEUED
        return self.get(spec.job_id)

    def get(self, job_id: str) -> JobStatus:
        if job_id not in self.submitted:
            raise JobNotFoundError(job_id)
        spec = self.submitted[job_id]
        return JobStatus(
            job_id=job_id,
            uid=f"uid-{job_id}",
            state=self.states[job_id],
            machine=spec.machine.name,
            spot=spec.spot,
            created_at=NOW,
        )

    def cancel(self, job_id: str) -> None:
        self.get(job_id)
        self.cancelled.append(job_id)


class FakeArtifactStore:
    def __init__(self) -> None:
        self.uploads: dict[str, Path] = {}
        self.metrics: dict[str, Mapping[str, Any]] = {}

    def upload_source(self, job_id: str, source: Path) -> str:
        self.uploads[job_id] = source
        return f"gs://work/runs/{job_id}/{source.name}"

    def runner_env(self, job_id: str) -> Mapping[str, str]:
        return {"DUCKLESS_JOB_ID": job_id, "DUCKLESS_BUCKET": "work"}

    def read_metrics(self, job_id: str) -> Mapping[str, Any] | None:
        return self.metrics.get(job_id)


class FakeLogReader:
    def __init__(self) -> None:
        self.lines: dict[str, tuple[LogLine, ...]] = {}

    def read(self, status: JobStatus, since: datetime | None = None, limit: int = 200) -> tuple[LogLine, ...]:
        return tuple(line for line in self.lines.get(status.uid, ()) if since is None or line.at > since)[:limit]


class FakeQuotaReader:
    def __init__(self, quotas: Mapping[str, Quota]) -> None:
        self.quotas = quotas

    def regional_quotas(self, region: str) -> Mapping[str, Quota]:
        return self.quotas


@pytest.fixture
def executor() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture
def artifact_store() -> FakeArtifactStore:
    return FakeArtifactStore()


@pytest.fixture
def log_reader() -> FakeLogReader:
    return FakeLogReader()


@pytest.fixture
def sql_file(tmp_path: Path) -> Path:
    path = tmp_path / "daily_revenue.sql"
    path.write_text("SELECT 42;")
    return path
