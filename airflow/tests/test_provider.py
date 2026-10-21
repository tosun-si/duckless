import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import duckless_airflow
import pytest
from airflow.exceptions import AirflowException, TaskDeferred
from duckless_airflow.operators import DuckLessRunOperator, job_summary, resolve_source
from duckless_airflow.triggers import DuckLessJobTrigger

import duckless
from duckless.core.job import JobReport, JobState, JobStatus
from duckless.core.routing import ExecutorKind

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
STATUS = JobStatus("dl-checks-1", "uid", JobState.QUEUED, "n2-highmem-8", False, NOW, executor=ExecutorKind.CLOUD_RUN)
METRICS = {"seconds": 41.2, "peak_rss_gb": 3.1, "bigquery_writes": [{"table": "p.d.t", "rows": 10}], "error": None}


def report(state: JobState, metrics: dict | None = None) -> JobReport:
    return JobReport(replace(STATUS, state=state, run_seconds=41.0), metrics)


class FakeClient:
    """The duckless.Client surface the provider uses, with scripted states."""

    def __init__(self, states: list[JobState], metrics: dict | None = METRICS) -> None:
        self.states = list(states)
        self.metrics = metrics
        self.runs: list[dict] = []
        self.cancelled: list[str] = []

    def run(self, source, **kwargs):
        self.runs.append({"source": source, **kwargs})
        return STATUS

    def status(self, job_id):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return report(state, self.metrics if state.is_terminal else None)

    def wait(self, job_id, *, poll_seconds=10, timeout_seconds=None, check=False):
        final = report(self.states[-1], self.metrics)
        if check and final.status.state is not JobState.SUCCEEDED:
            raise duckless.JobFailedError(final)
        return final

    def cancel(self, job_id):
        self.cancelled.append(job_id)


@pytest.fixture
def client(monkeypatch) -> FakeClient:
    fake = FakeClient([JobState.SUCCEEDED])
    monkeypatch.setattr(duckless.Client, "from_env", classmethod(lambda cls, env=None, **overrides: fake))
    return fake


class TestHelpers:
    @pytest.mark.parametrize(
        ("source", "dag_file", "expected"),
        [
            ("jobs/checks.py", "/home/airflow/gcs/dags/dq.py", "/home/airflow/gcs/dags/jobs/checks.py"),
            ("/opt/jobs/checks.py", "/home/airflow/gcs/dags/dq.py", "/opt/jobs/checks.py"),
            ("gs://b/jobs/checks.sql", "/home/airflow/gcs/dags/dq.py", "gs://b/jobs/checks.sql"),
            (None, "/dags/dq.py", None),
        ],
    )
    def test_given_a_source_when_resolving_then_relative_to_the_dag_folder(self, source, dag_file, expected) -> None:
        # when / then
        assert resolve_source(source, dag_file) == expected

    def test_given_a_report_when_summarizing_then_small_and_json_safe(self) -> None:
        # when
        summary = job_summary(report(JobState.SUCCEEDED, METRICS))

        # then
        assert summary == {
            "job_id": "dl-checks-1",
            "state": "SUCCEEDED",
            "executor": "cloudrun",
            "machine": "n2-highmem-8",
            "seconds": 41.2,
            "peak_rss_gb": 3.1,
            "bigquery_writes": [{"table": "p.d.t", "rows": 10}],
        }

    def test_given_the_package_when_airflow_reads_it_then_provider_info(self) -> None:
        # when
        info = duckless_airflow.get_provider_info()

        # then
        assert info["package-name"] == "airflow-provider-duckless" and info["versions"]


class TestTrigger:
    def test_given_a_trigger_when_serialized_then_rebuilt_identically(self) -> None:
        # given
        trigger = DuckLessJobTrigger("dl-checks-1", {"project": "acme"}, poll_seconds=5)

        # when
        path, kwargs = trigger.serialize()

        # then
        assert path == "duckless_airflow.triggers.DuckLessJobTrigger"
        assert DuckLessJobTrigger(**kwargs).serialize() == (path, kwargs)

    def test_given_a_running_job_when_triggering_then_one_event_at_the_end(self, monkeypatch) -> None:
        # given
        fake = FakeClient([JobState.RUNNING, JobState.RUNNING, JobState.SUCCEEDED])
        monkeypatch.setattr(duckless.Client, "from_env", classmethod(lambda cls, env=None, **o: fake))

        # when
        events = asyncio.run(collect(DuckLessJobTrigger("dl-checks-1", poll_seconds=0)))

        # then
        assert [e.payload for e in events] == [{"job_id": "dl-checks-1", "state": "SUCCEEDED"}]

    def test_given_a_failure_while_polling_when_triggering_then_an_error_event(self, monkeypatch) -> None:
        # given
        def broken(cls, env=None, **o):
            raise duckless.DucklessError("missing settings: DUCKLESS_BUCKET")

        monkeypatch.setattr(duckless.Client, "from_env", classmethod(broken))

        # when
        events = asyncio.run(collect(DuckLessJobTrigger("dl-checks-1", poll_seconds=0)))

        # then
        assert events[0].payload["state"] == "ERROR" and "DUCKLESS_BUCKET" in events[0].payload["error"]


async def collect(trigger: DuckLessJobTrigger) -> list:
    return [event async for event in trigger.run()]


class TestOperator:
    def test_given_a_job_when_executing_deferred_then_submits_and_defers_to_the_trigger(self, client) -> None:
        # given
        task = DuckLessRunOperator(task_id="checks", source="/jobs/checks.py", machine="n2-highmem-8", env={"DAY": 1})

        # when
        with pytest.raises(TaskDeferred) as deferred:
            task.execute(context={})

        # then
        assert client.runs[0]["source"] == "/jobs/checks.py" and client.runs[0]["env"] == {"DAY": "1"}
        assert client.runs[0]["name"] == "checks"
        assert deferred.value.trigger.job_id == "dl-checks-1" and deferred.value.method_name == "execute_complete"

    def test_given_a_succeeded_event_when_completing_then_the_summary_goes_to_xcom(self, client) -> None:
        # given
        task = DuckLessRunOperator(task_id="checks", source="/jobs/checks.py")

        # when
        summary = task.execute_complete(context={}, event={"job_id": "dl-checks-1", "state": "SUCCEEDED"})

        # then
        assert summary["state"] == "SUCCEEDED" and summary["seconds"] == 41.2

    def test_given_a_failed_job_when_completing_then_the_task_fails_with_the_runner_error(self, monkeypatch) -> None:
        # given
        fake = FakeClient([JobState.FAILED], metrics={"error": "ValueError: 3 rows share a key"})
        monkeypatch.setattr(duckless.Client, "from_env", classmethod(lambda cls, env=None, **o: fake))
        task = DuckLessRunOperator(task_id="checks", source="/jobs/checks.py")

        # when / then
        with pytest.raises(AirflowException, match="3 rows share a key"):
            task.execute_complete(context={}, event={"job_id": "dl-checks-1", "state": "FAILED"})

    def test_given_an_error_event_when_completing_then_the_task_fails(self, client) -> None:
        # when / then
        with pytest.raises(AirflowException, match="could not follow"):
            DuckLessRunOperator(task_id="checks", source="/j.py").execute_complete(
                context={}, event={"job_id": "dl-checks-1", "state": "ERROR", "error": "boom"}
            )

    def test_given_sync_mode_when_executing_then_waits_in_the_worker(self, client) -> None:
        # when
        summary = DuckLessRunOperator(task_id="checks", source="/j.py", deferrable=False).execute(context={})

        # then
        assert summary["state"] == "SUCCEEDED"

    def test_given_a_submitted_job_when_the_task_is_killed_then_the_job_is_cancelled(self, client) -> None:
        # given
        task = DuckLessRunOperator(task_id="checks", source="/j.py", deferrable=False)
        task.execute(context={})

        # when
        task.on_kill()

        # then
        assert client.cancelled == ["dl-checks-1"]

    def test_given_nothing_submitted_when_killed_then_nothing_cancelled(self, client) -> None:
        # when
        DuckLessRunOperator(task_id="checks", source="/j.py").on_kill()

        # then
        assert client.cancelled == []
