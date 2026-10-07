"""Executor on Cloud Run Jobs: one Cloud Run job per DuckLess job, sized like the requested machine.

Cloud Run cannot change CPU or memory per execution, so each DuckLess job gets its own Cloud Run
job (named after the job id) and a single execution. It starts in seconds but has no local disk:
DuckDB spills to an in-memory /tmp, so it is meant for jobs that fit in memory.
"""

from datetime import UTC, datetime

from google.api_core.exceptions import NotFound
from google.cloud import run_v2

from duckless.core.errors import JobNotFoundError
from duckless.core.job import JobEvent, JobKind, JobSpec, JobState, JobStatus
from duckless.core.routing import ExecutorKind, cloud_run_shape
from duckless.settings import Settings

# No local disk: leave room for the in-memory /tmp and Python next to DuckDB.
MEMORY_FRACTION = "0.6"
# Measured on Cloud Run: the first GCS call over gRPC waits ~37 s before falling back (51.7 s vs
# 14.5 s for the same job), so Cloud Run jobs default to HTTP.
GCS_GRPC = "false"


# ---------- pure ----------


def container_invocation(spec: JobSpec) -> tuple[list[str], list[str]]:
    """(command, args), same contract as Batch: a command job replaces the image entrypoint."""
    if spec.kind is JobKind.COMMAND:
        return [spec.command[0]], list(spec.command[1:])
    return [], list(spec.runner_args)


def build_job(spec: JobSpec, settings: Settings) -> run_v2.Job:
    shape = cloud_run_shape(spec.machine)
    if shape is None:
        raise ValueError(f"{spec.machine.name} does not fit Cloud Run Jobs")
    command, args = container_invocation(spec)
    defaults = {
        "DUCKLESS_MEMORY_FRACTION": MEMORY_FRACTION,
        "DUCKLESS_GCS_GRPC": GCS_GRPC,
        # The container sees the host's CPUs; DuckDB must use the vCPUs the job pays for.
        "DUCKLESS_THREADS": str(shape.cpu),
    }
    env = {**defaults, **dict(spec.env), "GOOGLE_CLOUD_PROJECT": settings.project}
    container = run_v2.Container(
        image=spec.image,
        command=command,
        args=args,
        env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
        resources=run_v2.ResourceRequirements(limits=shape.limits),
    )
    return run_v2.Job(
        labels={"app": "duckless", "duckless-kind": spec.kind.value},
        template=run_v2.ExecutionTemplate(
            task_count=1,
            template=run_v2.TaskTemplate(
                containers=[container],
                max_retries=0,  # no Spot preemption here; a failed job is a real failure
                timeout=f"{spec.max_run_seconds}s",
                service_account=settings.service_account,
                execution_environment=run_v2.ExecutionEnvironment.EXECUTION_ENVIRONMENT_GEN2,
            ),
        ),
    )


def execution_path(job: run_v2.Job) -> str | None:
    """Full resource name of the job's latest execution (the job only keeps its short name)."""
    name = job.latest_created_execution.name
    if not name:
        return None
    return name if name.startswith("projects/") else f"{job.name}/executions/{name}"


def execution_state(execution: run_v2.Execution | None) -> JobState:
    if execution is None:
        return JobState.QUEUED
    if execution.succeeded_count >= max(execution.task_count, 1):
        return JobState.SUCCEEDED
    if execution.cancelled_count:
        return JobState.CANCELLED
    if execution.failed_count:
        return JobState.FAILED
    if execution.running_count:
        return JobState.RUNNING
    return JobState.SCHEDULED


def status_from(job: run_v2.Job, execution: run_v2.Execution | None, machine: str) -> JobStatus:
    start, end = (execution.start_time, execution.completion_time) if execution else (None, None)
    run_seconds = (end - start).total_seconds() if start and end and end.year > 1970 else None
    events = tuple(
        JobEvent(
            at=c.last_transition_time,
            description=f"{c.type_}: {c.state.name.removeprefix('CONDITION_')} {c.message}".strip(),
        )
        for c in (execution.conditions if execution else ())
        if c.last_transition_time and c.last_transition_time.year > 1970
    )
    return JobStatus(
        job_id=job.name.rsplit("/", 1)[-1],
        uid=execution.name.rsplit("/", 1)[-1] if execution else "",
        state=execution_state(execution),
        machine=machine,
        spot=False,
        created_at=job.create_time or datetime.now(UTC),
        run_seconds=run_seconds,
        events=events,
        executor=ExecutorKind.CLOUD_RUN,
    )


# ---------- adapter ----------


class CloudRunExecutor:
    def __init__(self, jobs: run_v2.JobsClient, executions: run_v2.ExecutionsClient, settings: Settings) -> None:
        self._jobs = jobs
        self._executions = executions
        self._settings = settings

    def _parent(self) -> str:
        return f"projects/{self._settings.project}/locations/{self._settings.region}"

    def _job(self, job_id: str) -> run_v2.Job:
        try:
            return self._jobs.get_job(name=f"{self._parent()}/jobs/{job_id}")
        except NotFound as e:
            raise JobNotFoundError(job_id) from e

    def _latest_execution(self, job: run_v2.Job) -> run_v2.Execution | None:
        name = execution_path(job)
        return self._executions.get_execution(name=name) if name else None

    def submit(self, spec: JobSpec) -> JobStatus:
        self._jobs.create_job(parent=self._parent(), job_id=spec.job_id, job=build_job(spec, self._settings)).result()
        self._jobs.run_job(name=f"{self._parent()}/jobs/{spec.job_id}")  # returns once the execution exists
        return self.get(spec.job_id)

    def get(self, job_id: str) -> JobStatus:
        job = self._job(job_id)
        shape = job.template.template.containers[0].resources.limits
        return status_from(
            job, self._latest_execution(job), f"cloudrun {shape.get('cpu')} vCPU / {shape.get('memory')}"
        )

    def cancel(self, job_id: str) -> None:
        execution = self._latest_execution(self._job(job_id))
        if execution is not None:
            self._executions.cancel_execution(name=execution.name)
