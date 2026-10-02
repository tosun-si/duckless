from google.cloud import batch_v1

from duckless.adapters.batch import build_job, status_from_job
from duckless.core.job import JobKind, JobSpec, JobState
from duckless.core.machine import MachineType
from duckless.settings import Settings

SETTINGS = Settings.from_env(
    {"DUCKLESS_PROJECT": "acme", "DUCKLESS_BUCKET": "work", "DUCKLESS_SA": "runner@acme.iam", "DUCKLESS_IMAGE": "img"}
)


def spec(local_ssd_count: int = 4, spot: bool = True) -> JobSpec:
    return JobSpec(
        job_id="dl-job-1",
        kind=JobKind.SQL,
        image="img",
        machine=MachineType.parse("n2-highmem-32"),
        spot=spot,
        local_ssd_count=local_ssd_count,
        max_run_seconds=600,
        source_uri="gs://work/job.sql",
        env=(("DUCKLESS_JOB_ID", "dl-job-1"),),
    )


class TestBuildJob:
    def test_given_spot_job_with_local_ssd_when_building_then_vm_disk_and_scratch_mount_are_set(self) -> None:
        # when
        job = build_job(spec(), SETTINGS)

        # then
        policy = job.allocation_policy.instances[0].policy
        task = job.task_groups[0].task_spec
        assert policy.machine_type == "n2-highmem-32"
        assert policy.provisioning_model == batch_v1.AllocationPolicy.ProvisioningModel.SPOT
        assert policy.disks[0].new_disk.size_gb == 1500
        assert task.volumes[0].mount_path == "/mnt/disks/scratch"
        assert len(task.runnables) == 2  # scratch permissions, then the runner

    def test_given_job_when_building_then_runner_gets_args_env_and_project(self) -> None:
        # when
        runner = build_job(spec(), SETTINGS).task_groups[0].task_spec.runnables[-1]

        # then
        assert list(runner.container.commands) == ["sql", "gs://work/job.sql"]
        assert dict(runner.environment.variables) == {"DUCKLESS_JOB_ID": "dl-job-1", "GOOGLE_CLOUD_PROJECT": "acme"}

    def test_given_default_settings_when_building_then_vm_has_no_external_ip_on_full_network_paths(self) -> None:
        # when
        nic = build_job(spec(), SETTINGS).allocation_policy.network.network_interfaces[0]

        # then
        assert nic.no_external_ip_address
        assert nic.network == "projects/acme/global/networks/default"
        assert nic.subnetwork == "projects/acme/regions/europe-west1/subnetworks/default"

    def test_given_job_when_building_then_single_lifecycle_policy_fails_fast_on_runner_errors(self) -> None:
        # when
        task = build_job(spec(), SETTINGS).task_groups[0].task_spec

        # then
        assert len(task.lifecycle_policies) == 1  # Batch rejects more than one
        assert task.lifecycle_policies[0].action == batch_v1.LifecyclePolicy.Action.FAIL_TASK

    def test_given_no_local_ssd_when_building_then_no_disk_no_volume_single_runnable(self) -> None:
        # when
        job = build_job(spec(local_ssd_count=0, spot=False), SETTINGS)

        # then
        task = job.task_groups[0].task_spec
        assert list(job.allocation_policy.instances[0].policy.disks) == []
        assert list(task.volumes) == []
        assert len(task.runnables) == 1


class TestStatusFromJob:
    def test_given_batch_job_when_mapping_then_status_carries_id_uid_state(self) -> None:
        # given
        job = build_job(spec(), SETTINGS)
        job.name = "projects/acme/locations/europe-west1/jobs/dl-job-1"
        job.uid = "dl-job-1-uid"
        job.status.state = batch_v1.JobStatus.State.RUNNING

        # when
        status = status_from_job(job)

        # then
        assert (status.job_id, status.uid, status.state) == ("dl-job-1", "dl-job-1-uid", JobState.RUNNING)
        assert status.spot
