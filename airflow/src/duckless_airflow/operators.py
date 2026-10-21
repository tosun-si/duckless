"""DuckLessRunOperator: submits a DuckLess job and waits for it, deferred by default."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from airflow.exceptions import AirflowException

try:  # Airflow 3
    from airflow.sdk import BaseOperator
except ImportError:  # Airflow 2
    from airflow.models import BaseOperator

import duckless
from duckless_airflow.triggers import DuckLessJobTrigger

# Once a job has ended, its run duration and metrics land within seconds.
SETTLE_TIMEOUT_SECONDS = 120


def resolve_source(source: str | None, dag_file: str | None) -> str | None:
    """A relative source is relative to the DAG file's folder (gs:// and absolute paths stay as they are)."""
    if source is None or source.startswith("gs://") or Path(source).is_absolute() or not dag_file:
        return source
    return str(Path(dag_file).parent / source)


def job_summary(report: duckless.JobReport) -> dict[str, Any]:
    """What the task pushes to XCom: small, JSON-safe, enough for the tasks downstream."""
    metrics = report.metrics or {}
    return {
        "job_id": report.status.job_id,
        "state": report.status.state.value,
        "executor": report.status.executor.value,
        "machine": report.status.machine,
        "seconds": metrics.get("seconds"),
        "peak_rss_gb": metrics.get("peak_rss_gb"),
        "bigquery_writes": list(metrics.get("bigquery_writes") or ()),
    }


class DuckLessRunOperator(BaseOperator):
    """Runs a DuckLess job: a `.sql` or `.py` file for the runner, or an image and a command.

    Deferred by default: once the job is submitted, the task waits in the triggerer and frees its
    worker slot. The task fails when the job fails, with the runner's error. `duckless_settings`
    overrides the DUCKLESS_* settings of the environment (project, region, bucket,
    service_account, image).
    """

    template_fields: Sequence[str] = ("source", "env", "machine", "image", "command", "name")
    ui_color = "#2f7bff"

    def __init__(
        self,
        *,
        source: str | None = None,
        machine: str = "n2-highmem-16",
        spot: bool = False,
        local_ssd_count: int | None = None,
        env: Mapping[str, str] | None = None,
        on: str = "auto",
        image: str | None = None,
        command: Sequence[str] = (),
        name: str | None = None,
        max_run_seconds: int = 3 * 3600,
        duckless_settings: Mapping[str, str] | None = None,
        deferrable: bool = True,
        poll_seconds: float = 30,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.source = source
        self.machine = machine
        self.spot = spot
        self.local_ssd_count = local_ssd_count
        self.env = dict(env or {})
        self.on = on
        self.image = image
        self.command = list(command)
        self.name = name
        self.max_run_seconds = max_run_seconds
        self.duckless_settings = dict(duckless_settings or {})
        self.deferrable = deferrable
        self.poll_seconds = poll_seconds
        self.job_id: str | None = None

    def _client(self) -> duckless.Client:
        return duckless.Client.from_env(**self.duckless_settings)

    def execute(self, context: Any) -> dict[str, Any] | None:
        client = self._client()
        # Airflow 3 raises on `self.dag` for a task outside a DAG (tests, a bare operator).
        dag_file = self.dag.fileloc if self.has_dag() else None
        status = client.run(
            resolve_source(self.source, dag_file),
            machine=self.machine,
            spot=self.spot,
            local_ssd_count=self.local_ssd_count,
            env={k: str(v) for k, v in self.env.items()},
            on=self.on,
            image=self.image,
            command=tuple(self.command),
            name=self.name or self.task_id,
            max_run_seconds=self.max_run_seconds,
        )
        self.job_id = status.job_id
        self.log.info("DuckLess job %s submitted on %s (%s)", status.job_id, status.executor.value, status.machine)
        if self.deferrable:
            self.defer(
                trigger=DuckLessJobTrigger(status.job_id, self.duckless_settings, self.poll_seconds),
                method_name="execute_complete",
            )
        return self._finish(client, status.job_id, timeout_seconds=None)

    def execute_complete(self, context: Any, event: dict[str, Any]) -> dict[str, Any]:
        self.job_id = event["job_id"]
        if event["state"] == "ERROR":
            raise AirflowException(f"could not follow DuckLess job {event['job_id']}: {event.get('error')}")
        return self._finish(self._client(), event["job_id"], timeout_seconds=SETTLE_TIMEOUT_SECONDS)

    def _finish(self, client: duckless.Client, job_id: str, timeout_seconds: float | None) -> dict[str, Any]:
        try:
            report = client.wait(job_id, poll_seconds=self.poll_seconds, timeout_seconds=timeout_seconds, check=True)
        except duckless.JobFailedError as failed:
            raise AirflowException(str(failed)) from failed
        summary = job_summary(report)
        self.log.info("DuckLess job %s %s in %s s", job_id, summary["state"], summary["seconds"])
        return summary

    def on_kill(self) -> None:
        """Synchronous mode: a killed task cancels its job, so no VM keeps running for nothing."""
        if self.job_id:
            self.log.info("Cancelling DuckLess job %s", self.job_id)
            self._client().cancel(self.job_id)
