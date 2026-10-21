"""Python API: run DuckLess jobs from code (orchestrators, scripts, notebooks).

    import duckless

    client = duckless.Client.from_env()       # the DUCKLESS_* settings, as the CLI reads them
    job = client.run("checks.py", machine="n2-highmem-8", spot=True, env={"DAY": "2026-10-20"})
    report = client.wait(job.job_id, check=True)
    print(report.status.state, report.metrics["seconds"])

The same services as the CLI, without the terminal: nothing is printed, results are immutable.
"""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from duckless.core.errors import DucklessError
from duckless.core.job import JobReport, JobRequest, JobState, JobStatus, LogLine
from duckless.core.routing import Placement
from duckless.settings import Settings
from duckless.wiring import Services, gcp_services

POLL_SECONDS = 10
# Batch fills the run duration and the last events a few seconds after the final state.
SETTLE_SECONDS = 5
SETTLE_ATTEMPTS = 6


class JobFailedError(DucklessError):
    """`wait(check=True)` on a job that did not succeed."""

    def __init__(self, report: JobReport) -> None:
        error = (report.metrics or {}).get("error")
        super().__init__(f"job {report.status.job_id} {report.status.state}" + (f": {error}" if error else ""))
        self.report = report


class JobTimeoutError(DucklessError):
    """`wait(timeout_seconds=...)` ran out before the job ended. The job keeps running: cancel it if needed."""

    def __init__(self, job_id: str, state: JobState, timeout_seconds: float) -> None:
        super().__init__(f"job {job_id} still {state} after {timeout_seconds:g} s")
        self.job_id = job_id


def wait_for_job(
    get_job: Callable[[str], JobReport],
    job_id: str,
    *,
    poll_seconds: float = POLL_SECONDS,
    timeout_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> JobReport:
    """Polls until the job ends, then until its run duration is filled in; never cancels it."""
    deadline = None if timeout_seconds is None else clock() + timeout_seconds
    report = get_job(job_id)
    while not report.status.state.is_terminal:
        if deadline is not None and clock() >= deadline:
            raise JobTimeoutError(job_id, report.status.state, timeout_seconds)
        sleep(poll_seconds)
        report = get_job(job_id)
    for _ in range(SETTLE_ATTEMPTS):
        if report.status.run_seconds is not None:
            break
        sleep(SETTLE_SECONDS)
        report = get_job(job_id)
    return report


@dataclass(frozen=True, slots=True)
class Client:
    services: Services

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: str | None) -> "Client":
        """Settings from DUCKLESS_* (the process environment by default); keyword overrides win:
        project, region, bucket, service_account, image, network, subnetwork."""
        return cls(gcp_services(Settings.from_env(os.environ if env is None else env, **overrides)))

    def run(
        self,
        source: str | Path | None = None,
        *,
        machine: str = "n2-highmem-16",
        spot: bool = False,
        local_ssd_count: int | None = None,
        env: Mapping[str, str] | None = None,
        on: str = "auto",
        image: str | None = None,
        command: tuple[str, ...] | list[str] = (),
        max_run_seconds: int = 3 * 3600,
        name: str | None = None,
    ) -> JobStatus:
        """Submits a job and returns at once: a .sql or .py file for the runner, or an image and a command."""
        request = JobRequest(
            machine=machine,
            source=Path(source) if source is not None else None,
            image=image,
            command=tuple(command),
            spot=spot,
            local_ssd_count=local_ssd_count,
            env=dict(env or {}),
            max_run_seconds=max_run_seconds,
            name=name,
            placement=Placement(on),
        )
        return self.services.run_job(request)

    def status(self, job_id: str) -> JobReport:
        """State and timeline; the runner's metrics once the job is over."""
        return self.services.get_job(job_id)

    def wait(
        self,
        job_id: str,
        *,
        poll_seconds: float = POLL_SECONDS,
        timeout_seconds: float | None = None,
        check: bool = False,
    ) -> JobReport:
        """Blocks until the job ends. `check=True` raises JobFailedError unless it succeeded."""
        report = wait_for_job(self.services.get_job, job_id, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
        if check and report.status.state is not JobState.SUCCEEDED:
            raise JobFailedError(report)
        return report

    def cancel(self, job_id: str) -> None:
        self.services.cancel_job(job_id)

    def logs(self, job_id: str, since: datetime | None = None) -> tuple[LogLine, ...]:
        """The runner's log lines (Cloud Logging), oldest first."""
        return self.services.job_logs(job_id, since)
