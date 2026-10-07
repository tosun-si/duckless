from collections.abc import Callable
from pathlib import Path

import pytest

from duckless import service
from duckless.core.errors import InvalidJobError, InvalidLocalSsdCountError, JobNotFoundError
from duckless.core.job import JobKind, JobRequest, JobState, JobStatus, LogLine
from duckless.core.quota import Quota
from duckless.core.routing import ExecutorKind
from tests.conftest import NOW, FakeArtifactStore, FakeExecutor, FakeLogReader, FakeQuotaReader

DEFAULT_IMAGE = "europe-docker.pkg.dev/duckless/runner:0.1.0"
NEW_PROJECT_QUOTAS = {
    "CPUS": Quota(4, 300),
    "N2_CPUS": Quota(0, 200),
    "PREEMPTIBLE_CPUS": Quota(0, 0),
    "LOCAL_SSD_TOTAL_GB": Quota(0, 9e18),
}


@pytest.fixture
def run(executor: FakeExecutor, artifact_store: FakeArtifactStore) -> Callable[[JobRequest], JobStatus]:
    return lambda request: service.run_job(
        request,
        executor=executor,
        artifact_store=artifact_store,
        default_image=DEFAULT_IMAGE,
        clock=lambda: NOW,
        nonce=lambda: "abcd1234",
    )


class TestRunJob:
    def test_given_sql_file_when_running_then_submits_uploaded_source_on_default_image(
        self, run: Callable[[JobRequest], JobStatus], executor: FakeExecutor, sql_file: Path
    ) -> None:
        # when
        status = run(JobRequest(machine="n2-highmem-32", source=sql_file, spot=True))

        # then
        spec = executor.submitted[status.job_id]
        assert status.job_id == "dl-daily-revenue-20261002-093000-abcd"
        assert spec.kind is JobKind.SQL
        assert spec.image == DEFAULT_IMAGE
        assert spec.runner_args == ("sql", f"gs://work/runs/{status.job_id}/daily_revenue.sql")
        assert spec.spot

    def test_given_machine_that_fits_cloud_run_when_running_then_submitted_on_cloud_run_without_ssd(
        self, run: Callable[[JobRequest], JobStatus], executor: FakeExecutor, sql_file: Path
    ) -> None:
        # when
        status = run(JobRequest(machine="n2-standard-8", source=sql_file))

        # then
        spec = executor.submitted[status.job_id]
        assert spec.executor is ExecutorKind.CLOUD_RUN
        assert spec.local_ssd_count == 0

    def test_given_no_local_ssd_count_when_running_then_uses_smallest_count_the_machine_accepts(
        self, run: Callable[[JobRequest], JobStatus], executor: FakeExecutor, sql_file: Path
    ) -> None:
        # when
        status = run(JobRequest(machine="n2-highmem-32", source=sql_file))

        # then
        assert executor.submitted[status.job_id].local_ssd_count == 4

    def test_given_user_env_overlapping_runner_env_when_running_then_runner_keys_win(
        self, run: Callable[[JobRequest], JobStatus], executor: FakeExecutor, sql_file: Path
    ) -> None:
        # given
        request = JobRequest(machine="n2-highmem-16", source=sql_file, env={"DUCKLESS_BUCKET": "x", "TPCH_SF": "100"})

        # when
        status = run(request)

        # then
        env = dict(executor.submitted[status.job_id].env)
        assert env["DUCKLESS_BUCKET"] == "work"
        assert env["TPCH_SF"] == "100"

    def test_given_image_and_command_when_running_then_submits_command_without_upload(
        self, run: Callable[[JobRequest], JobStatus], executor: FakeExecutor, artifact_store: FakeArtifactStore
    ) -> None:
        # when
        status = run(JobRequest(machine="n2-highmem-16", image="acme/dbt:1.2", command=("dbt", "build")))

        # then
        spec = executor.submitted[status.job_id]
        assert spec.kind is JobKind.COMMAND
        assert spec.runner_args == ("dbt", "build")
        assert spec.image == "acme/dbt:1.2"
        assert artifact_store.uploads == {}

    def test_given_bad_local_ssd_count_when_running_then_raises_before_uploading(
        self,
        run: Callable[[JobRequest], JobStatus],
        executor: FakeExecutor,
        artifact_store: FakeArtifactStore,
        sql_file: Path,
    ) -> None:
        # when / then
        with pytest.raises(InvalidLocalSsdCountError):
            run(JobRequest(machine="n2-highmem-16", source=sql_file, local_ssd_count=1))
        assert artifact_store.uploads == {}
        assert executor.submitted == {}

    @pytest.mark.parametrize(
        "request_",
        [
            JobRequest(machine="n2-highmem-16"),
            JobRequest(machine="n2-highmem-16", source=Path("job.txt")),
            JobRequest(machine="n2-highmem-16", source=Path("job.sql"), command=("dbt", "build")),
        ],
        ids=["nothing", "unsupported-suffix", "both"],
    )
    def test_given_invalid_request_when_running_then_raises_invalid_job(
        self, run: Callable[[JobRequest], JobStatus], request_: JobRequest
    ) -> None:
        # when / then
        with pytest.raises(InvalidJobError):
            run(request_)


@pytest.fixture
def job_id(run: Callable[[JobRequest], JobStatus], sql_file: Path) -> str:
    return run(JobRequest(machine="n2-highmem-16", source=sql_file)).job_id


class TestGetJob:
    def test_given_running_job_when_getting_then_metrics_are_not_read(
        self, executor: FakeExecutor, artifact_store: FakeArtifactStore, job_id: str
    ) -> None:
        # given
        executor.states[job_id] = JobState.RUNNING
        artifact_store.metrics[job_id] = {"status": "succeeded"}

        # when
        report = service.get_job(job_id, executor=executor, artifact_store=artifact_store)

        # then
        assert report.status.state is JobState.RUNNING
        assert report.metrics is None

    def test_given_finished_job_when_getting_then_report_carries_runner_metrics(
        self, executor: FakeExecutor, artifact_store: FakeArtifactStore, job_id: str
    ) -> None:
        # given
        executor.states[job_id] = JobState.SUCCEEDED
        artifact_store.metrics[job_id] = {"status": "succeeded", "seconds": 44.4}

        # when
        report = service.get_job(job_id, executor=executor, artifact_store=artifact_store)

        # then
        assert report.metrics == {"status": "succeeded", "seconds": 44.4}

    def test_given_unknown_job_when_getting_then_raises_not_found(
        self, executor: FakeExecutor, artifact_store: FakeArtifactStore
    ) -> None:
        # when / then
        with pytest.raises(JobNotFoundError):
            service.get_job("dl-nope", executor=executor, artifact_store=artifact_store)


class TestJobLogsAndCancel:
    def test_given_job_logs_when_reading_then_lines_are_looked_up_by_job_uid(
        self, executor: FakeExecutor, log_reader: FakeLogReader, job_id: str
    ) -> None:
        # given
        line = LogLine(at=NOW, severity="INFO", message="statement_done")
        log_reader.lines[f"uid-{job_id}"] = (line,)

        # when
        lines = service.job_logs(job_id, executor=executor, log_reader=log_reader)

        # then
        assert lines == (line,)

    def test_given_submitted_job_when_cancelling_then_executor_cancels_it(
        self, executor: FakeExecutor, job_id: str
    ) -> None:
        # when
        service.cancel_job(job_id, executor=executor)

        # then
        assert executor.cancelled == [job_id]


class TestPreflight:
    def test_given_enough_quota_when_checking_spot_n2_then_ok_on_standard_quotas(self) -> None:
        # when
        report = service.preflight(
            "n2-highmem-32",
            spot=True,
            local_ssd_count=None,
            quota_reader=FakeQuotaReader(NEW_PROJECT_QUOTAS),
            region="europe-west1",
        )

        # then
        assert report.ok
        assert {c.name for c in report.checks} == {"machine", "quota CPUS", "quota N2_CPUS", "quota LOCAL_SSD_TOTAL_GB"}

    def test_given_too_few_cpus_when_checking_then_blocked_on_family_quota(self) -> None:
        # given
        quotas = FakeQuotaReader(NEW_PROJECT_QUOTAS | {"N2_CPUS": Quota(180, 200)})

        # when
        report = service.preflight(
            "n2-highmem-32", spot=False, local_ssd_count=None, quota_reader=quotas, region="europe-west1"
        )

        # then
        assert not report.ok
        assert [c.name for c in report.checks if not c.ok] == ["quota N2_CPUS"]

    def test_given_bad_local_ssd_count_when_checking_then_blocked_on_machine(self) -> None:
        # when
        report = service.preflight(
            "n2-highmem-16", spot=False, local_ssd_count=1, quota_reader=FakeQuotaReader({}), region="europe-west1"
        )

        # then
        assert not report.ok
        assert [c.name for c in report.checks] == ["machine"]
