"""Runner logs from Cloud Logging: Batch writes task stdout to `batch_task_logs`, labelled by job uid."""

from datetime import datetime

from google.cloud import logging as cloud_logging

from duckless.core.job import JobStatus, LogLine
from duckless.core.routing import ExecutorKind

TASK_LOG = "batch_task_logs"


def log_filter(project: str, status: JobStatus, since: datetime | None) -> str:
    where = (
        (
            'resource.type="cloud_run_job"',
            f'labels."run.googleapis.com/execution_name"="{status.uid}"',
            'logName:"run.googleapis.com%2Fstd"',  # the container's stdout/stderr, not the platform events
        )
        if status.executor is ExecutorKind.CLOUD_RUN
        else (f'logName="projects/{project}/logs/{TASK_LOG}"', f'labels.job_uid="{status.uid}"')
    )
    return " AND ".join((*where, *((f'timestamp>"{since.isoformat()}"',) if since else ())))


def to_line(entry: cloud_logging.LogEntry) -> LogLine:
    """The runner logs JSON lines (jsonPayload); anything else (image pull, crash) is plain text."""
    payload = entry.payload if isinstance(entry.payload, dict) else {"message": str(entry.payload)}
    fields = {k: v for k, v in payload.items() if k not in {"message", "severity", "time"}}
    return LogLine(
        at=entry.timestamp,
        severity=entry.severity or "DEFAULT",
        message=str(payload.get("message", "")),
        fields=fields,
    )


class CloudLoggingLogReader:
    def __init__(self, client: cloud_logging.Client, project: str) -> None:
        self._client = client
        self._project = project

    def read(self, status: JobStatus, since: datetime | None = None, limit: int = 200) -> tuple[LogLine, ...]:
        entries = self._client.list_entries(
            resource_names=[f"projects/{self._project}"],
            filter_=log_filter(self._project, status, since),
            order_by=cloud_logging.ASCENDING,
            max_results=limit,
        )
        return tuple(map(to_line, entries))
