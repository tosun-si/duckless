---
name: troubleshooting
description: Diagnose a failed, stuck or slow DuckLess job or a failing `duckless init`, from `duckless status`, `logs` and `result` to the fix. Use when a job is FAILED, stays QUEUED or SCHEDULED, is slower than expected, or when init/destroy errors.
---

# Troubleshooting DuckLess

## Read before guessing

```bash
duckless status <job-id>   # state, runner, machine, events, runner metrics (peak rss, spill)
duckless logs <job-id>     # runner logs (add -f to follow)
duckless result <job-id>   # last SELECT of a SQL job
```

The events in `status` come from the platform (Batch or Cloud Run): scheduling, image pull,
exit code. The logs come from the runner: `duckdb_connected` (threads, memory, spill dir),
one `statement_done` per statement, then `job_done` or `job_failed` with the error.

## Known causes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `init` ends `BLOCKED` | an organization policy forbids something | read the failing check; fix the policy or the region; nothing was created |
| `init` fails on IAM right after creating an account | IAM propagation | run `init` again (it is idempotent) |
| Stays `QUEUED`/`SCHEDULED` on Batch | no capacity or quota for the machine (Spot especially) | `duckless preflight -m …`; another size or family, or drop `--spot` |
| Fails in seconds, no runner logs, event mentions image pull | runner account cannot read the image | grant `roles/artifactregistry.reader` on the image's repository |
| Exit code from Batch with a preemption event | Spot VM reclaimed | it is retried; use on-demand for deadline jobs |
| `Out of Memory` / killed | working set larger than memory, or spill disk full | bigger highmem machine; more `--local-ssd`; on Cloud Run use `--on batch` |
| `gs://` errors about credentials or HTTP | `httpfs` installed or loaded by the job | remove it: the runner serves `gs://` with the `gcs` extension |
| First GCS call very slow on Cloud Run | gRPC to GCS from Cloud Run | already HTTP by default there; do not set `DUCKLESS_GCS_GRPC=true` |
| `Too many open files` | old runner image | runner ≥ 0.2.0 raises the limit |
| DuckLake `ATTACH` fails | catalog unreachable | see the `ducklake` skill: network, `.envrc` lines, Cloud Run VPC egress |
| Slow writes to GCS | single Parquet writer | `PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB'` |
| BigQuery `Permission Denied` | dataset not granted, or a query (`ATTACH`, `bigquery_query`) without `--bigquery-jobs` | `duckless init --bigquery-dataset <dataset>` (`--bigquery-jobs` for queries); datasets of other projects: their owners grant `roles/bigquery.dataViewer` |
| Slow BigQuery read | large table on Cloud Run | `--on batch`, select fewer columns, add a `filter` |
| Slow Python | row-by-row UDF | vectorized (Arrow) UDF or SQL |

## When it is none of these

Reproduce small: same job on a sample with `-m n2-standard-4` (Cloud Run, ~30 s round trip),
then scale back up. Check `status` metrics before blaming the machine: no spill and low peak
RSS mean the time goes elsewhere (GCS reads, many small files, a Python loop).

Docs: https://tosun-si.github.io/duckless/
