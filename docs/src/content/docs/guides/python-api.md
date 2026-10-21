---
title: "Python API: run DuckLess jobs from code"
description: Submit, wait for and cancel DuckLess jobs from Python, in an orchestrator such as Airflow or Cloud Composer, a script or a notebook.
---

Everything `duckless run`, `status`, `logs` and `cancel` do is available from Python, without the
terminal. It's how an orchestrator, a script or a notebook runs DuckLess jobs.

```python
import duckless

client = duckless.Client.from_env()  # the DUCKLESS_* settings, as the CLI reads them
job = client.run("checks.py", machine="n2-highmem-8", spot=True, env={"DAY": "2026-10-20"})
report = client.wait(job.job_id, check=True)

print(report.status.state, report.metrics["seconds"])
```

## The client

`duckless.Client.from_env()` reads the same `DUCKLESS_*` settings as the CLI. Keyword arguments
override them, and a mapping replaces the environment:

```python
client = duckless.Client.from_env(project="my-project", region="europe-west1")
client = duckless.Client.from_env(
    {"DUCKLESS_PROJECT": "my-project", "DUCKLESS_BUCKET": "…", "DUCKLESS_SA": "…", "DUCKLESS_IMAGE": "…"}
)
```

Nothing is called on Google Cloud until the first job: building the client is free. It uses the
caller's credentials (Application Default Credentials), which need the
[job submitter roles](/duckless/reference/requirements/#4-job-submitters).

| Method | Returns | |
| --- | --- | --- |
| `run(source, *, machine, spot, local_ssd_count, env, on, name, max_run_seconds)` | `JobStatus` | submits a `.sql` or `.py` file and returns at once; `on` is `auto`, `batch` or `cloudrun` |
| `run(image=…, command=[…], …)` | `JobStatus` | runs any image with a command, like `duckless exec` |
| `status(job_id)` | `JobReport` | state, timeline, and the runner's metrics once the job is over |
| `wait(job_id, *, poll_seconds, timeout_seconds, check)` | `JobReport` | blocks until the job ends |
| `cancel(job_id)` | `None` | cancels a queued or running job |
| `logs(job_id, since)` | `tuple[LogLine, …]` | the runner's log lines, oldest first |

## Waiting and failures

`wait` returns the final report, whatever the outcome. With `check=True`, it raises
`duckless.JobFailedError` unless the job succeeded; the message carries the runner's error and the
exception keeps the report:

```python
try:
    client.wait(job.job_id, check=True)
except duckless.JobFailedError as failed:
    print(failed.report.metrics["error"])
```

`timeout_seconds` bounds the wait, not the job: `duckless.JobTimeoutError` is raised and the job
keeps running. Cancel it if that's what you want; the job's own limit is `max_run_seconds` (3 hours
by default).

```python
try:
    client.wait(job.job_id, timeout_seconds=1800)
except duckless.JobTimeoutError:
    client.cancel(job.job_id)
    raise
```

`report.metrics` is the job's `metrics.json`: duration and memory, each SQL statement with its
duration and last rows, and what ran (image, versions, source sha256, BigQuery writes).

## From Airflow (Cloud Composer)

Install `duckless` in the environment (Composer: *PyPI packages*), set the `DUCKLESS_*` settings as
environment variables, and give the environment's service account the
[job submitter roles](/duckless/reference/requirements/#4-job-submitters) (`job_submitters` in the
Terraform module). A task then runs a job:

```python
import pendulum
from airflow.decorators import dag, task


@dag(schedule="0 6 * * *", start_date=pendulum.datetime(2026, 10, 1, tz="UTC"), catchup=False)
def daily_quality_checks():
    @task
    def run_checks(ds=None) -> dict:
        import duckless

        client = duckless.Client.from_env()
        job = client.run("/home/airflow/gcs/dags/jobs/checks.py", machine="n2-highmem-8", env={"DAY": ds})
        try:
            report = client.wait(job.job_id, check=True)
        except BaseException:
            client.cancel(job.job_id)  # the task failed or was killed: don't leave the job running
            raise
        return {"job_id": job.job_id, "seconds": report.metrics["seconds"]}

    run_checks()


daily_quality_checks()
```

The task waits in a worker slot while the job runs. That's fine for jobs of a few minutes; for
long jobs, a deferrable operator that waits in the triggerer instead is planned.
