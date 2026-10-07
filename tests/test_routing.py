from datetime import UTC, datetime

import pytest
from google.cloud import run_v2

from duckless.adapters.cloud_logging import log_filter
from duckless.adapters.cloud_run import (
    build_job,
    container_invocation,
    execution_path,
    execution_state,
    status_from,
)
from duckless.core.errors import InvalidJobError, JobNotFoundError
from duckless.core.job import JobKind, JobSpec, JobState, JobStatus
from duckless.core.machine import MachineType, memory_gb
from duckless.core.routing import CloudRunShape, ExecutorKind, Placement, choose_executor, cloud_run_shape
from duckless.executor_router import RoutingExecutor
from duckless.settings import Settings
from tests.conftest import NOW, FakeExecutor

SETTINGS = Settings.from_env(
    {"DUCKLESS_PROJECT": "acme", "DUCKLESS_BUCKET": "work", "DUCKLESS_SA": "runner@acme.iam", "DUCKLESS_IMAGE": "img"}
)


def machine(name: str) -> MachineType:
    return MachineType.parse(name)


class TestMemory:
    @pytest.mark.parametrize(
        ("name", "gb"),
        [
            ("n2-standard-8", 32),
            ("n2-highmem-16", 128),
            ("e2-highcpu-8", 8),
            ("c3-highcpu-8", 16),
            ("m3-megamem-64", None),
        ],
    )
    def test_given_machine_when_computing_memory_then_matches_its_kind(self, name: str, gb: float | None) -> None:
        # when / then
        assert memory_gb(machine(name)) == gb


class TestCloudRunShape:
    @pytest.mark.parametrize(
        ("name", "shape"),
        [
            ("n2-standard-2", CloudRunShape(2, 8)),
            ("n2-standard-8", CloudRunShape(8, 32)),
            ("n2-highmem-4", CloudRunShape(8, 32)),  # 32 GiB needs 8 CPU on Cloud Run
            ("e2-highcpu-8", CloudRunShape(8, 8)),
            ("n2-highmem-8", None),  # 64 GB
            ("m3-megamem-64", None),  # memory unknown
        ],
    )
    def test_given_machine_when_sizing_for_cloud_run_then_smallest_valid_shape(
        self, name: str, shape: CloudRunShape | None
    ) -> None:
        # when / then
        assert cloud_run_shape(machine(name)) == shape


class TestChooseExecutor:
    @pytest.mark.parametrize(
        ("name", "spot", "ssd", "expected"),
        [
            ("n2-standard-8", False, None, ExecutorKind.CLOUD_RUN),
            ("n2-standard-8", True, None, ExecutorKind.BATCH),  # Spot only exists on Batch
            ("n2-standard-8", False, 1, ExecutorKind.BATCH),  # so does local SSD
            ("n2-highmem-16", False, None, ExecutorKind.BATCH),  # too big for Cloud Run
        ],
    )
    def test_given_auto_when_choosing_then_cloud_run_only_when_nothing_changes_for_the_job(
        self, name: str, spot: bool, ssd: int | None, expected: ExecutorKind
    ) -> None:
        # when / then
        assert choose_executor(Placement.AUTO, machine(name), spot, ssd) is expected

    def test_given_batch_placement_when_choosing_then_batch_even_if_it_fits_cloud_run(self) -> None:
        # when / then
        assert choose_executor(Placement.BATCH, machine("n2-standard-2"), False, None) is ExecutorKind.BATCH

    @pytest.mark.parametrize(("name", "spot"), [("n2-highmem-16", False), ("n2-standard-8", True)])
    def test_given_cloud_run_placement_that_cannot_hold_when_choosing_then_raises(self, name: str, spot: bool) -> None:
        # when / then
        with pytest.raises(InvalidJobError):
            choose_executor(Placement.CLOUD_RUN, machine(name), spot, None)


def cloud_run_spec(kind: JobKind = JobKind.SQL) -> JobSpec:
    return JobSpec(
        job_id="dl-job-1",
        kind=kind,
        image="img",
        machine=machine("n2-standard-8"),
        spot=False,
        local_ssd_count=0,
        max_run_seconds=600,
        source_uri="gs://work/job.sql" if kind is not JobKind.COMMAND else None,
        command=("dbt", "build") if kind is JobKind.COMMAND else (),
        env=(("DUCKLESS_JOB_ID", "dl-job-1"),),
        executor=ExecutorKind.CLOUD_RUN,
    )


class TestCloudRunAdapter:
    def test_given_sql_job_when_building_then_sized_container_with_runner_args_and_env(self) -> None:
        # when
        job = build_job(cloud_run_spec(), SETTINGS)

        # then
        task = job.template.template
        container = task.containers[0]
        assert dict(container.resources.limits) == {"cpu": "8", "memory": "32Gi"}
        assert list(container.command) == [] and list(container.args) == ["sql", "gs://work/job.sql"]
        env = {e.name: e.value for e in container.env}
        assert env["DUCKLESS_JOB_ID"] == "dl-job-1" and env["DUCKLESS_MEMORY_FRACTION"] == "0.6"
        assert (env["DUCKLESS_GCS_GRPC"], env["DUCKLESS_THREADS"]) == ("false", "8")
        assert (task.service_account, task.max_retries) == ("runner@acme.iam", 0)

    def test_given_command_job_when_invoking_then_command_replaces_the_entrypoint(self) -> None:
        # when / then
        assert container_invocation(cloud_run_spec(JobKind.COMMAND)) == (["dbt"], ["build"])

    def test_given_cloud_run_spec_with_local_ssd_when_building_spec_then_rejected(self) -> None:
        # when / then
        with pytest.raises(InvalidJobError):
            JobSpec(**{**{f: getattr(cloud_run_spec(), f) for f in JobSpec.__slots__}, "local_ssd_count": 1})

    @pytest.mark.parametrize(
        ("counts", "state"),
        [
            (None, JobState.QUEUED),
            ({"task_count": 1}, JobState.SCHEDULED),
            ({"task_count": 1, "running_count": 1}, JobState.RUNNING),
            ({"task_count": 1, "succeeded_count": 1}, JobState.SUCCEEDED),
            ({"task_count": 1, "failed_count": 1}, JobState.FAILED),
            ({"task_count": 1, "cancelled_count": 1}, JobState.CANCELLED),
        ],
    )
    def test_given_execution_counts_when_mapping_then_job_state(self, counts: dict | None, state: JobState) -> None:
        # when / then
        assert execution_state(run_v2.Execution(**counts) if counts is not None else None) is state

    def test_given_short_execution_name_when_building_path_then_full_resource_name(self) -> None:
        # given: what the API returns in Job.latest_created_execution
        job = run_v2.Job(
            name="projects/acme/locations/europe-west1/jobs/dl-job-1",
            latest_created_execution=run_v2.ExecutionReference(name="dl-job-1-x7k2p"),
        )

        # when / then
        assert execution_path(job) == "projects/acme/locations/europe-west1/jobs/dl-job-1/executions/dl-job-1-x7k2p"
        assert execution_path(run_v2.Job(name="projects/acme/locations/r/jobs/j")) is None

    def test_given_finished_execution_when_mapping_status_then_cloud_run_status_with_duration(self) -> None:
        # given
        job = run_v2.Job(name="projects/acme/locations/europe-west1/jobs/dl-job-1")
        execution = run_v2.Execution(
            name="projects/acme/locations/europe-west1/jobs/dl-job-1/executions/dl-job-1-x7k2p",
            task_count=1,
            succeeded_count=1,
            start_time=datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC),
            completion_time=datetime(2026, 10, 7, 10, 0, 42, tzinfo=UTC),
        )

        # when
        status = status_from(job, execution, "cloudrun 8 vCPU / 32Gi")

        # then
        assert (status.job_id, status.uid, status.state) == ("dl-job-1", "dl-job-1-x7k2p", JobState.SUCCEEDED)
        assert (status.run_seconds, status.executor) == (42.0, ExecutorKind.CLOUD_RUN)


class TestLogFilter:
    def test_given_cloud_run_job_when_filtering_then_execution_stdout(self) -> None:
        # given
        status = JobStatus("dl-1", "dl-1-x7k2p", JobState.RUNNING, "m", False, NOW, executor=ExecutorKind.CLOUD_RUN)

        # when
        query = log_filter("acme", status, None)

        # then
        assert 'resource.type="cloud_run_job"' in query
        assert 'labels."run.googleapis.com/execution_name"="dl-1-x7k2p"' in query

    def test_given_batch_job_when_filtering_then_task_logs_by_uid(self) -> None:
        # when
        query = log_filter("acme", JobStatus("dl-1", "uid-1", JobState.RUNNING, "m", False, NOW), None)

        # then
        assert 'labels.job_uid="uid-1"' in query and "batch_task_logs" in query


class TestRoutingExecutor:
    def test_given_specs_for_both_executors_when_submitting_then_each_goes_to_its_own(self) -> None:
        # given
        batch, cloud_run = FakeExecutor(), FakeExecutor()
        router = RoutingExecutor({ExecutorKind.BATCH: lambda: batch, ExecutorKind.CLOUD_RUN: lambda: cloud_run})

        # when
        router.submit(cloud_run_spec())

        # then
        assert list(cloud_run.submitted) == ["dl-job-1"] and batch.submitted == {}

    def test_given_job_on_second_executor_when_getting_then_found_there(self) -> None:
        # given
        batch, cloud_run = FakeExecutor(), FakeExecutor()
        router = RoutingExecutor({ExecutorKind.BATCH: lambda: batch, ExecutorKind.CLOUD_RUN: lambda: cloud_run})
        router.submit(cloud_run_spec())

        # when / then
        assert router.get("dl-job-1").job_id == "dl-job-1"
        router.cancel("dl-job-1")
        assert cloud_run.cancelled == ["dl-job-1"]

    def test_given_unknown_job_when_getting_then_not_found(self) -> None:
        # given
        router = RoutingExecutor({ExecutorKind.BATCH: FakeExecutor, ExecutorKind.CLOUD_RUN: FakeExecutor})

        # when / then
        with pytest.raises(JobNotFoundError):
            router.get("dl-nope")

    def test_given_batch_only_usage_when_routing_then_cloud_run_client_never_built(self) -> None:
        # given
        built: list[str] = []
        router = RoutingExecutor(
            {
                ExecutorKind.BATCH: lambda: built.append("batch") or FakeExecutor(),
                ExecutorKind.CLOUD_RUN: lambda: built.append("cloudrun") or FakeExecutor(),
            }
        )
        spec = cloud_run_spec()
        batch_spec = JobSpec(
            **{**{f: getattr(spec, f) for f in JobSpec.__slots__}, "executor": ExecutorKind.BATCH, "local_ssd_count": 1}
        )

        # when
        router.submit(batch_spec)

        # then
        assert built == ["batch"]
