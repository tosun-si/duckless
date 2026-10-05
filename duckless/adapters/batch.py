"""Executor on Cloud Batch: one task, one VM per job, deleted when the job ends."""

from google.api_core.exceptions import NotFound
from google.cloud import batch_v1

from duckless.core.errors import JobNotFoundError
from duckless.core.job import JobEvent, JobKind, JobSpec, JobState, JobStatus
from duckless.core.machine import LOCAL_SSD_GB
from duckless.settings import Settings

SCRATCH_PATH = "/mnt/disks/scratch"
SCRATCH_DEVICE = "scratch"
# Runner exit codes (job failed / bad usage): fail at once, keep retries for infra failures
# such as a Spot preemption (exit 50001). Batch allows a single lifecycle policy per task.
RUNNER_EXIT_CODES = (1, 2)
MAX_RETRIES = 2

_STATES = {
    batch_v1.JobStatus.State.QUEUED: JobState.QUEUED,
    batch_v1.JobStatus.State.SCHEDULED: JobState.SCHEDULED,
    batch_v1.JobStatus.State.RUNNING: JobState.RUNNING,
    batch_v1.JobStatus.State.SUCCEEDED: JobState.SUCCEEDED,
    batch_v1.JobStatus.State.FAILED: JobState.FAILED,
    batch_v1.JobStatus.State.CANCELLED: JobState.CANCELLED,
}


# ---------- pure ----------


def container_invocation(spec: JobSpec) -> tuple[str, list[str]]:
    """(entrypoint, args). Runner jobs keep the image entrypoint (`python -m duckless_runtime`) and
    pass `sql|py <uri>`; a command job replaces the entrypoint, so `-- dbt build` runs dbt itself."""
    if spec.kind is JobKind.COMMAND:
        return spec.command[0], list(spec.command[1:])
    return "", list(spec.runner_args)


def build_job(spec: JobSpec, settings: Settings) -> batch_v1.Job:
    has_scratch = spec.local_ssd_count > 0
    entrypoint, args = container_invocation(spec)
    # Local SSD is mounted by Batch on the host as root; open it to the non-root runner user.
    prepare_scratch = batch_v1.Runnable(
        script=batch_v1.Runnable.Script(text=f"mkdir -p {SCRATCH_PATH} && chmod 1777 {SCRATCH_PATH}")
    )
    runner = batch_v1.Runnable(
        # Task volumes are bind-mounted into the container at the same path by default.
        container=batch_v1.Runnable.Container(image_uri=spec.image, entrypoint=entrypoint, commands=args),
        environment=batch_v1.Environment(variables={**dict(spec.env), "GOOGLE_CLOUD_PROJECT": settings.project}),
    )
    task = batch_v1.TaskSpec(
        runnables=[prepare_scratch, runner] if has_scratch else [runner],
        volumes=[batch_v1.Volume(device_name=SCRATCH_DEVICE, mount_path=SCRATCH_PATH)] if has_scratch else [],
        max_run_duration=f"{spec.max_run_seconds}s",
        max_retry_count=MAX_RETRIES,
        lifecycle_policies=[
            batch_v1.LifecyclePolicy(
                action=batch_v1.LifecyclePolicy.Action.FAIL_TASK,
                action_condition=batch_v1.LifecyclePolicy.ActionCondition(exit_codes=list(RUNNER_EXIT_CODES)),
            )
        ],
    )
    policy = batch_v1.AllocationPolicy.InstancePolicy(
        machine_type=spec.machine.name,
        provisioning_model=(
            batch_v1.AllocationPolicy.ProvisioningModel.SPOT
            if spec.spot
            else batch_v1.AllocationPolicy.ProvisioningModel.STANDARD
        ),
        disks=[
            batch_v1.AllocationPolicy.AttachedDisk(
                new_disk=batch_v1.AllocationPolicy.Disk(type_="local-ssd", size_gb=LOCAL_SSD_GB * spec.local_ssd_count),
                device_name=SCRATCH_DEVICE,
            )
        ]
        if has_scratch
        else [],
    )
    allocation = batch_v1.AllocationPolicy(
        # Any zone of the region: Spot + highmem + local SSD stocks out zone by zone.
        location=batch_v1.AllocationPolicy.LocationPolicy(allowed_locations=[f"regions/{settings.region}"]),
        instances=[batch_v1.AllocationPolicy.InstancePolicyOrTemplate(policy=policy)],
        service_account=batch_v1.ServiceAccount(email=settings.service_account),
        network=batch_v1.AllocationPolicy.NetworkPolicy(
            network_interfaces=[
                batch_v1.AllocationPolicy.NetworkInterface(
                    network=settings.network,
                    subnetwork=settings.subnetwork,
                    no_external_ip_address=not settings.external_ip,
                )
            ]
        ),
    )
    return batch_v1.Job(
        task_groups=[batch_v1.TaskGroup(task_spec=task, task_count=1)],
        allocation_policy=allocation,
        logs_policy=batch_v1.LogsPolicy(destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING),
        labels={"app": "duckless", "duckless-kind": spec.kind.value},
    )


def status_from_job(job: batch_v1.Job) -> JobStatus:
    policy = job.allocation_policy.instances[0].policy
    return JobStatus(
        job_id=job.name.rsplit("/", 1)[-1],
        uid=job.uid,
        state=_STATES.get(job.status.state, JobState.UNKNOWN),
        machine=policy.machine_type,
        spot=policy.provisioning_model == batch_v1.AllocationPolicy.ProvisioningModel.SPOT,
        created_at=job.create_time,
        run_seconds=job.status.run_duration.total_seconds() if job.status.run_duration else None,
        events=tuple(
            JobEvent(at=e.event_time, description=e.description.split(" for job ")[0]) for e in job.status.status_events
        ),
    )


# ---------- adapter ----------


class BatchExecutor:
    def __init__(self, client: batch_v1.BatchServiceClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _parent(self) -> str:
        return f"projects/{self._settings.project}/locations/{self._settings.region}"

    def _name(self, job_id: str) -> str:
        return f"{self._parent()}/jobs/{job_id}"

    def submit(self, spec: JobSpec) -> JobStatus:
        created = self._client.create_job(
            parent=self._parent(), job_id=spec.job_id, job=build_job(spec, self._settings)
        )
        return status_from_job(created)

    def get(self, job_id: str) -> JobStatus:
        try:
            return status_from_job(self._client.get_job(name=self._name(job_id)))
        except NotFound as e:
            raise JobNotFoundError(job_id) from e

    def cancel(self, job_id: str) -> None:
        try:
            self._client.cancel_job(name=self._name(job_id))
        except NotFound as e:
            raise JobNotFoundError(job_id) from e
