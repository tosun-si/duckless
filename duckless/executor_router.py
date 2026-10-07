"""One Executor in front of several: submits where the spec says, finds a job wherever it runs."""

from collections.abc import Callable, Mapping

from duckless.core.errors import JobNotFoundError
from duckless.core.job import JobSpec, JobStatus
from duckless.core.routing import ExecutorKind
from duckless.ports import Executor


class RoutingExecutor:
    """Executors are built on first use: a Batch-only user never creates a Cloud Run client."""

    def __init__(self, factories: Mapping[ExecutorKind, Callable[[], Executor]]) -> None:
        self._factories = factories
        self._built: dict[ExecutorKind, Executor] = {}

    def _executor(self, kind: ExecutorKind) -> Executor:
        if kind not in self._built:
            self._built[kind] = self._factories[kind]()
        return self._built[kind]

    def _first(self, job_id: str, action: Callable[[Executor], JobStatus | None]) -> JobStatus | None:
        for kind in self._factories:
            try:
                return action(self._executor(kind))
            except JobNotFoundError:
                continue
        raise JobNotFoundError(job_id)

    def submit(self, spec: JobSpec) -> JobStatus:
        return self._executor(spec.executor).submit(spec)

    def get(self, job_id: str) -> JobStatus:
        return self._first(job_id, lambda executor: executor.get(job_id))

    def cancel(self, job_id: str) -> None:
        self._first(job_id, lambda executor: executor.cancel(job_id))
