from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

import duckless
from duckless.api import JobFailedError, JobTimeoutError, wait_for_job
from duckless.core.job import JobReport, JobRequest, JobState, JobStatus, LogLine
from duckless.core.routing import Placement
from duckless.settings import SettingsError
from duckless.wiring import Services
from tests.conftest import NOW

STATUS = JobStatus("dl-job-1", "uid", JobState.QUEUED, "n2-highmem-8", False, NOW)


def report(state: JobState, run_seconds: float | None = 12.0, metrics: dict | None = None) -> JobReport:
    return JobReport(replace(STATUS, state=state, run_seconds=run_seconds), metrics)


def sequence(*reports: JobReport) -> Iterator[JobReport]:
    """One report per call, the last one repeated."""
    yield from reports
    while True:
        yield reports[-1]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestWaitForJob:
    def test_given_a_job_that_ends_when_waiting_then_polls_until_the_final_report(self) -> None:
        # given
        reports = sequence(report(JobState.QUEUED), report(JobState.RUNNING), report(JobState.SUCCEEDED))
        clock = FakeClock()

        # when
        final = wait_for_job(
            lambda _: next(reports), "dl-job-1", poll_seconds=10, sleep=clock.sleep, clock=lambda: clock.now
        )

        # then
        assert final.status.state is JobState.SUCCEEDED
        assert clock.sleeps == [10, 10]

    def test_given_a_final_state_without_duration_when_waiting_then_waits_for_it_to_settle(self) -> None:
        # given: Batch fills the run duration a few seconds after the final state
        reports = sequence(report(JobState.SUCCEEDED, run_seconds=None), report(JobState.SUCCEEDED, run_seconds=41.0))
        clock = FakeClock()

        # when
        final = wait_for_job(lambda _: next(reports), "dl-job-1", sleep=clock.sleep, clock=lambda: clock.now)

        # then
        assert final.status.run_seconds == 41.0

    def test_given_a_timeout_when_the_job_runs_longer_then_raises_without_cancelling(self) -> None:
        # given
        clock = FakeClock()

        # when / then
        with pytest.raises(JobTimeoutError, match="still RUNNING after 30 s"):
            wait_for_job(
                lambda _: report(JobState.RUNNING),
                "dl-job-1",
                poll_seconds=10,
                timeout_seconds=30,
                sleep=clock.sleep,
                clock=lambda: clock.now,
            )


def services(**overrides) -> tuple[Services, dict[str, list]]:
    """Fake services, and the calls they received."""
    calls: dict[str, list] = {"run": [], "cancel": []}
    base = Services(
        run_job=lambda request: calls["run"].append(request) or STATUS,
        get_job=lambda job_id: report(JobState.SUCCEEDED, metrics={"status": "succeeded"}),
        job_logs=lambda job_id, since: (LogLine(NOW, "INFO", "job_done"),),
        cancel_job=lambda job_id: calls["cancel"].append(job_id),
        preflight=lambda machine, spot, local_ssd_count: None,
    )
    return replace(base, **overrides), calls


class TestClient:
    def test_given_run_arguments_when_running_then_one_job_request_like_the_cli(self) -> None:
        # given
        calls: list[JobRequest] = []
        client = duckless.Client(services(run_job=lambda request: calls.append(request) or STATUS)[0])

        # when
        status = client.run(
            "checks.py", machine="n2-highmem-8", spot=True, env={"DAY": "2026-10-20"}, on="batch", name="dq"
        )

        # then
        assert status is STATUS
        request = calls[0]
        assert request.source == Path("checks.py") and request.machine == "n2-highmem-8" and request.spot
        assert dict(request.env) == {"DAY": "2026-10-20"} and request.placement is Placement.BATCH
        assert request.name == "dq"

    def test_given_an_image_and_command_when_running_then_no_source(self) -> None:
        # given
        calls: list[JobRequest] = []
        client = duckless.Client(services(run_job=lambda request: calls.append(request) or STATUS)[0])

        # when
        client.run(image="acme/dbt:1.2", command=["dbt", "build"])

        # then
        assert calls[0].source is None and calls[0].command == ("dbt", "build")

    def test_given_an_unknown_placement_when_running_then_refused_before_submitting(self) -> None:
        # when / then
        with pytest.raises(ValueError, match="kubernetes"):
            duckless.Client(services()[0]).run("job.sql", on="kubernetes")

    def test_given_a_failed_job_when_waiting_with_check_then_raises_with_the_runner_error(self, monkeypatch) -> None:
        # given
        failed = report(JobState.FAILED, metrics={"error": "ValueError: 3 rows share a key"})
        client = duckless.Client(services(get_job=lambda job_id: failed)[0])

        # when / then
        with pytest.raises(JobFailedError, match="FAILED: ValueError: 3 rows share a key") as raised:
            client.wait("dl-job-1", check=True)
        assert raised.value.report is failed

    def test_given_a_failed_job_when_waiting_without_check_then_returns_the_report(self) -> None:
        # given
        client = duckless.Client(services(get_job=lambda job_id: report(JobState.FAILED))[0])

        # when / then
        assert client.wait("dl-job-1").status.state is JobState.FAILED

    def test_given_a_job_when_cancelling_or_reading_logs_then_delegates(self) -> None:
        # given
        built, calls = services()
        client = duckless.Client(built)

        # when
        client.cancel("dl-job-1")
        logs = client.logs("dl-job-1")

        # then
        assert calls["cancel"] == ["dl-job-1"]
        assert logs[0].message == "job_done"

    def test_given_missing_settings_when_building_from_env_then_names_them(self) -> None:
        # when / then
        with pytest.raises(SettingsError, match="DUCKLESS_BUCKET"):
            duckless.Client.from_env({"DUCKLESS_PROJECT": "acme"})

    def test_given_settings_when_building_from_env_then_no_cloud_call_yet(self) -> None:
        # given: adapters are built lazily, on the first call
        env = {"DUCKLESS_PROJECT": "acme", "DUCKLESS_BUCKET": "b", "DUCKLESS_SA": "sa@acme", "DUCKLESS_IMAGE": "img"}

        # when
        client = duckless.Client.from_env(env, region="us-central1")

        # then
        assert isinstance(client.services, Services)
