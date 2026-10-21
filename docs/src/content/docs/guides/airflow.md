---
title: "Airflow and Cloud Composer: the DuckLess operator"
description: Run DuckLess jobs from Airflow DAGs with a deferrable operator that frees the worker while the job runs. Airflow 2.10 and 3, Cloud Composer.
---

`airflow-provider-duckless` adds one operator to Airflow: `DuckLessRunOperator`. It submits a
DuckLess job, then waits for it in the triggerer, so no worker slot is held while the job runs. The
task fails when the job fails, with the runner's error.

```python
import pendulum
from airflow import DAG
from duckless_airflow.operators import DuckLessRunOperator

with DAG(
    "daily_quality_checks", schedule="0 6 * * *", start_date=pendulum.datetime(2026, 10, 1, tz="UTC"), catchup=False
):
    checks = DuckLessRunOperator(
        task_id="checks",
        source="jobs/checks.py",  # next to the DAG file; or an absolute path, or gs://
        machine="n2-highmem-8",
        env={"DAY": "{{ ds }}"},
    )
```

## Install

```bash
pip install airflow-provider-duckless
```

It installs `duckless` too. Supported: Airflow 2.10 and later, Airflow 3 included, on Python 3.11
to 3.13. Both are checked in CI, Airflow 2.10 with its official constraints file (the closest to a
Cloud Composer environment).

On **Cloud Composer**: add `airflow-provider-duckless` in *PyPI packages*, and the `DUCKLESS_*`
settings (`DUCKLESS_PROJECT`, `DUCKLESS_REGION`, `DUCKLESS_BUCKET`, `DUCKLESS_SA`,
`DUCKLESS_IMAGE`) in *Environment variables*: they are the lines `duckless init` or the Terraform
module print. The environment's service account needs the
[job submitter roles](/duckless/reference/requirements/#4-job-submitters) (`job_submitters` in the
module). The triggerer must be enabled (it is by default on Composer 3).

## Parameters

| Parameter | Default | |
| --- | --- | --- |
| `source` | | a `.sql` or `.py` file: relative to the DAG file, absolute, or `gs://` |
| `machine` | `n2-highmem-16` | the machine type; `on="auto"` sends it to Cloud Run Jobs when it fits there |
| `spot`, `local_ssd_count` | `False`, smallest valid | as for `duckless run` |
| `env` | `{}` | variables for the job (`${VAR}` in SQL, `os.environ` in Python); templated |
| `on` | `auto` | `auto`, `batch` or `cloudrun` |
| `image`, `command` | | any image and command, like `duckless exec`, instead of `source` |
| `name` | the task id | the job id prefix |
| `max_run_seconds` | 3 hours | the job's own time limit |
| `duckless_settings` | `{}` | overrides the `DUCKLESS_*` settings: `project`, `region`, `bucket`, `service_account`, `image` |
| `deferrable` | `True` | wait in the triggerer; `False` waits in the worker |
| `poll_seconds` | `30` | how often the job's state is read |

`source`, `env`, `machine`, `image`, `command` and `name` are templated (`{{ ds }}`, `{{ params.x }}`).

## What the task returns

The task pushes a small summary of the job to XCom, for the tasks downstream:

```json
{
  "job_id": "dl-checks-20261021-060012-3f2a",
  "state": "SUCCEEDED",
  "executor": "cloudrun",
  "machine": "cloudrun 2 vCPU / 8Gi",
  "seconds": 41.2,
  "peak_rss_gb": 3.1,
  "bigquery_writes": [{"table": "my-project.finance.lines", "mode": "replace_partitions", "rows": 500000, "bytes_billed": 31457280, "seconds": {"total": 9.8}}]
}
```

## Cancelling

With `deferrable=False`, a task killed in Airflow (marked failed, timed out) cancels its job, so no
machine keeps running. A deferred task waits in the triggerer, where Airflow gives an operator no
hook when it is killed: the job runs to its end, bounded by `max_run_seconds`. Cancel it with
`duckless cancel <job-id>` (the job id is in the task log) if needed.

On Airflow 2.10 environments, `google-cloud-batch` is pinned to 0.17.33, which can't cancel a Batch
job: cancelling one says so. Cloud Run jobs cancel normally.
