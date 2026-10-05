"""`duckless` CLI: a driving adapter, translates args <-> service calls and renders the results."""

import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from duckless.core.errors import DucklessError
from duckless.core.infra import InfraRequest, InfraStatus, default_runner_tag, envrc_lines
from duckless.core.job import JobReport, JobRequest, JobState, LogLine
from duckless.core.preflight import PreflightReport
from duckless.settings import Settings, SettingsError
from duckless.wiring import InfraServices, Services, cli_version, gcp_infra_services, gcp_services

POLL_SECONDS = 10
SETTLE_SECONDS = 5
PREVIEW_COLUMN_WIDTH = 24

app = typer.Typer(help="Serverless DuckDB on GCP.", no_args_is_help=True, pretty_exceptions_enable=False)

Machine = Annotated[str, typer.Option("--machine", "-m", help="Compute Engine machine type, e.g. n2-highmem-32")]
Spot = Annotated[bool, typer.Option("--spot", help="Spot VM (cheaper, may be preempted and retried)")]
LocalSsd = Annotated[
    int | None, typer.Option("--local-ssd", help="375 GB local SSDs for spill (default: smallest allowed)")
]
Env = Annotated[list[str] | None, typer.Option("--env", "-e", help="KEY=VALUE passed to the job, repeatable")]
MaxRun = Annotated[int, typer.Option("--max-run-seconds", help="Hard limit on the job duration")]
Wait = Annotated[bool, typer.Option("--wait/--no-wait", help="Follow the job until it ends")]


# ---------- rendering (pure) ----------


def report_lines(report: JobReport) -> list[str]:
    status = report.status
    run = f"{status.run_seconds:.1f}s" if status.run_seconds is not None else "-"
    head = [
        f"{status.job_id}  {status.state}",
        f"  machine {status.machine}{' (spot)' if status.spot else ''}  run {run}",
    ]
    events = [f"  {e.at:%H:%M:%S}  {e.description}" for e in status.events]
    return head + events + metrics_lines(report.metrics)


def metrics_lines(metrics: Mapping[str, Any] | None) -> list[str]:
    if not metrics:
        return []
    summary = (
        f"  runner {metrics.get('status')}  {metrics.get('seconds')}s"
        f"  peak rss {metrics.get('peak_rss_gb')} GB  spill {metrics.get('peak_spill_gb')} GB"
    )
    error = [f"  error: {metrics['error']}"] if metrics.get("error") else []
    steps = [
        f"  {s['seconds']:>9.2f}s  {s['type']:<7} {' '.join(s['sql'].split())[:80]}" for s in metrics.get("steps", ())
    ]
    return [summary, *error, *steps]


def last_preview(metrics: Mapping[str, Any] | None) -> list[list[str]]:
    """Rows of the last statement that returned some (the runner keeps up to 20)."""
    steps = (metrics or {}).get("steps", ())
    return next((list(s["preview"]) for s in reversed(steps) if s.get("preview")), [])


def table_lines(rows: Iterable[Iterable[str]]) -> list[str]:
    return [" | ".join(str(c)[:PREVIEW_COLUMN_WIDTH].ljust(PREVIEW_COLUMN_WIDTH) for c in row).rstrip() for row in rows]


def log_line(line: LogLine) -> str:
    extras = " ".join(f"{k}={v}" for k, v in line.fields.items() if k in {"seconds", "type", "error", "status"})
    return f"{line.at:%H:%M:%S} {line.severity:<7} {line.message} {extras}".rstrip()


def preflight_lines(report: PreflightReport) -> list[str]:
    checks = [f"  {'ok  ' if c.ok else 'FAIL'}  {c.name:<28} {c.detail}" for c in report.checks]
    return [f"preflight {report.region}: {'ok' if report.ok else 'blocked'}", *checks]


def infra_lines(status: InfraStatus) -> list[str]:
    if not status.ok:
        return [f"{status.deployment}: {status.state}", *(f"  {line}" for line in (status.error or "").splitlines())]
    envrc = envrc_lines(status)
    return [f"{status.deployment}: {status.state}", *(["", "# add to .envrc:", *envrc] if envrc else [])]


def parse_env(pairs: list[str] | None) -> dict[str, str]:
    bad = [p for p in pairs or () if "=" not in p]
    if bad:
        raise typer.BadParameter(f"expected KEY=VALUE, got {bad}", param_hint="--env")
    return dict(p.split("=", 1) for p in pairs or ())


# ---------- effects ----------


@dataclass
class CliContext:
    """Options from the command line; job settings are only read by the commands that need them."""

    project: str | None
    region: str | None
    _services: Services | None = field(default=None, repr=False)

    def services(self) -> Services:
        if self._services is None:
            try:
                settings = Settings.from_env(os.environ, project=self.project, region=self.region)
            except SettingsError as e:
                typer.echo(f"{e}\nrun `duckless init` first, it prints these lines.", err=True)
                raise typer.Exit(2) from e
            self._services = gcp_services(settings)
        return self._services

    def infra_request(self, **fields) -> InfraRequest:
        project = self.project or os.environ.get("DUCKLESS_PROJECT")
        region = self.region or os.environ.get("DUCKLESS_REGION", "europe-west1")
        if not project:
            raise typer.BadParameter("give --project or set DUCKLESS_PROJECT", param_hint="--project")
        return InfraRequest(project=project, region=region, **fields)


def _services(ctx: typer.Context) -> Services:
    return ctx.obj.services()


def _infra() -> InfraServices:
    return gcp_infra_services()


def _echo(lines: list[str]) -> None:
    typer.echo("\n".join(lines))


def _settled(services: Services, report: JobReport, attempts: int = 6) -> JobReport:
    """Batch fills run_duration and the last events a few seconds after the final state."""
    for _ in range(attempts):
        if report.status.run_seconds is not None:
            return report
        time.sleep(SETTLE_SECONDS)
        report = services.get_job(report.status.job_id)
    return report


def _follow(services: Services, job_id: str) -> int:
    last_state = None
    while True:
        report = services.get_job(job_id)
        if report.status.state != last_state:
            typer.echo(f"{datetime.now():%H:%M:%S}  {report.status.state}")
            last_state = report.status.state
        if report.status.state.is_terminal:
            final = _settled(services, report)
            _echo(report_lines(final))
            return 0 if final.status.state is JobState.SUCCEEDED else 1
        time.sleep(POLL_SECONDS)


def _submit(ctx: typer.Context, request: JobRequest, wait: bool) -> None:
    services = _services(ctx)
    try:
        status = services.run_job(request)
    except DucklessError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    typer.echo(f"submitted {status.job_id}  ({status.machine}{' spot' if status.spot else ''})")
    if wait:
        raise typer.Exit(_follow(services, status.job_id))


@app.callback()
def main_options(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="GCP project (default: $DUCKLESS_PROJECT)")] = None,
    region: Annotated[str | None, typer.Option(help="Region (default: $DUCKLESS_REGION or europe-west1)")] = None,
) -> None:
    ctx.obj = CliContext(project=project, region=region)


@app.command()
def init(
    ctx: typer.Context,
    data_bucket: Annotated[
        list[str] | None, typer.Option("--data-bucket", help="Bucket jobs may read/write, repeatable")
    ] = None,
    runner_tag: Annotated[
        str | None, typer.Option(help="Runner image tag (default: the CLI version, edge for dev builds)")
    ] = None,
    name: Annotated[str, typer.Option(help="Prefix of the created resources")] = "duckless",
) -> None:
    """Deploy (or upgrade) DuckLess in a project with Infrastructure Manager."""
    request = ctx.obj.infra_request(
        data_buckets=tuple(data_bucket or ()),
        runner_image_tag=runner_tag or default_runner_tag(cli_version()),
        name=name,
    )
    typer.echo(f"duckless init: {request.project} ({request.region})")
    status = _infra().init(request, lambda step: typer.echo(f"  - {step}"))
    _echo(infra_lines(status))
    raise typer.Exit(0 if status.ok else 1)


@app.command()
def destroy(
    ctx: typer.Context,
    name: Annotated[str, typer.Option(help="Prefix given to init")] = "duckless",
    force: Annotated[bool, typer.Option("--force", help="Also delete a non-empty work bucket")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation")] = False,
) -> None:
    """Delete everything `duckless init` created in the project."""
    request = ctx.obj.infra_request(name=name, force_destroy=force)
    if not yes:
        typer.confirm(f"Delete the DuckLess deployment '{name}' in {request.project}?", abort=True)
    if force:
        typer.echo("  - re-applying with force_destroy so the work bucket can be deleted")
        _infra().init(request, lambda step: typer.echo(f"    {step}"))
    status = _infra().destroy(request, lambda step: typer.echo(f"  - {step}"))
    _echo(infra_lines(status))
    raise typer.Exit(0 if status.ok else 1)


@app.command()
def run(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="job.sql or job.py")],
    machine: Machine = "n2-highmem-16",
    spot: Spot = False,
    local_ssd: LocalSsd = None,
    env: Env = None,
    max_run_seconds: MaxRun = 3 * 3600,
    wait: Wait = True,
) -> None:
    """Run a .sql or .py file on the default runner (DuckDB + GCS via ADC)."""
    request = JobRequest(
        machine=machine,
        source=source,
        spot=spot,
        local_ssd_count=local_ssd,
        env=parse_env(env),
        max_run_seconds=max_run_seconds,
    )
    _submit(ctx, request, wait)


@app.command("exec")
def exec_(
    ctx: typer.Context,
    command: Annotated[list[str], typer.Argument(help="Command run in the image, after --")],
    image: Annotated[str, typer.Option(help="Image extending the DuckLess runner image")],
    machine: Machine = "n2-highmem-16",
    spot: Spot = False,
    local_ssd: LocalSsd = None,
    env: Env = None,
    max_run_seconds: MaxRun = 3 * 3600,
    wait: Wait = True,
) -> None:
    """Run your own image + command, e.g. `duckless exec --image … -- dbt build`."""
    request = JobRequest(
        machine=machine,
        image=image,
        command=tuple(command),
        spot=spot,
        local_ssd_count=local_ssd,
        env=parse_env(env),
        max_run_seconds=max_run_seconds,
    )
    _submit(ctx, request, wait)


@app.command()
def status(ctx: typer.Context, job_id: str) -> None:
    """Job state, timeline and, once over, the runner's metrics."""
    _echo(report_lines(_services(ctx).get_job(job_id)))


@app.command()
def logs(
    ctx: typer.Context,
    job_id: str,
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
) -> None:
    """Runner logs from Cloud Logging."""
    services = _services(ctx)
    since = None
    while True:
        lines = services.job_logs(job_id, since)
        for line in lines:
            typer.echo(log_line(line))
        since = lines[-1].at if lines else since
        if not follow or services.get_job(job_id).status.state.is_terminal:
            return
        time.sleep(POLL_SECONDS)


@app.command()
def result(ctx: typer.Context, job_id: str) -> None:
    """Rows of the job's last SELECT (first 20)."""
    report = _services(ctx).get_job(job_id)
    if not report.status.state.is_terminal:
        typer.echo(f"{job_id} is still {report.status.state}", err=True)
        raise typer.Exit(1)
    _echo(table_lines(last_preview(report.metrics)) or ["(no rows)"])


@app.command()
def cancel(ctx: typer.Context, job_id: str) -> None:
    """Cancel a queued or running job."""
    _services(ctx).cancel_job(job_id)
    typer.echo(f"cancellation requested for {job_id}")


@app.command()
def preflight(
    ctx: typer.Context, machine: Machine = "n2-highmem-16", spot: Spot = False, local_ssd: LocalSsd = None
) -> None:
    """Check a machine choice against GCE rules and the region's quotas."""
    report = _services(ctx).preflight(machine, spot, local_ssd)
    _echo(preflight_lines(report))
    raise typer.Exit(0 if report.ok else 1)


def main() -> None:
    try:
        app()
    except DucklessError as e:
        typer.echo(f"error: {e}", err=True)
        raise SystemExit(2) from e
