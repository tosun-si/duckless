---
name: writing-jobs
description: Write DuckLess jobs (SQL files, Python scripts with duckless_runtime, or custom images with `duckless exec`) that read and write Parquet on GCS fast. Use when creating, reviewing or porting a data job for DuckLess, or when a job is slow on GCS writes or Python UDFs.
---

# Writing DuckLess jobs

Three kinds of job; pick the simplest that fits:

| Job | When | Command |
| --- | --- | --- |
| `.sql` file | transformations expressible in SQL (most) | `duckless run job.sql -m <machine>` |
| `.py` file | logic around queries, Python libraries, Arrow/NumPy UDFs | `duckless run job.py -m <machine>` |
| image + command | an existing tool (dbt-duckdb, a CLI) | `duckless exec --image <img> -m <machine> -- <cmd>` |

## SQL jobs

- Statements run top to bottom. `${VAR}` is replaced from the job env: `${DUCKLESS_BUCKET}`
  (work bucket), `${DUCKLESS_JOB_ID}`, and anything passed with `-e KEY=VALUE`.
- The last `SELECT` (20 rows) is shown by `duckless result <job-id>`: end with a small check
  query (counts, min/max) rather than a big result.
- Write results with `COPY … TO 'gs://…' (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB')`.

## Python jobs

```python
from duckless_runtime import connect, copy_to_parquet, log

con = connect()  # GCS auth, memory, threads, spill dir, DuckLake `lake` when configured
copy_to_parquet(con, "SELECT …", "gs://bucket/out/")
log("done", rows=123)  # structured log line, visible in `duckless logs`
```

## Rules that matter (measured, not taste)

- **Never install or load `httpfs`.** The runner serves `gs://` with the `gcs` extension and
  the job's service account. httpfs would take `gs://` over and fail on credentials.
- **Parallel writes.** A single Parquet writer caps around 110 MB/s to GCS; `PER_THREAD_OUTPUT`
  reached about 900 MB/s on 32 vCPU. `copy_to_parquet()` does it for Python.
- **No row-by-row Python UDFs.** They run around 8k rows/s. Use vectorized ones
  (`con.create_function(..., type="arrow")`) or rewrite in SQL.
- **No secrets in code or in DSNs.** Credentials come from the service account (ADC). DuckDB
  prints full connection strings in its errors, so a password in a DSN ends up in the logs.
- **Paths:** `gs://` for files; `gcss://` only for a DuckLake `DATA_PATH`.
- **Formats:** Parquet, CSV (`read_csv`) and JSON (`read_json`) are built in; Excel
  (`LOAD excel`, `read_xlsx`), Avro and Iceberg are in the runner image. Text formats are parsed
  in full on every read: convert CSV/JSON to Parquet or a DuckLake table once, then query that.
  Prefer newline-delimited JSON and many medium files over one big `.gz` (not split across threads).
  Other extensions (`delta`, `spatial`) need an image built `FROM` the runner.
- **BigQuery:** `bigquery_scan('project.dataset.table', filter = '…')` in SQL, `read_bigquery(con, table,
  columns, where)` in Python (a DuckDB relation: SQL, Arrow batches, pandas, Polars). Select the columns
  and filter: only those leave BigQuery. Needs `init --bigquery-dataset <dataset>`; large tables
  read much faster on Cloud Batch (`--on batch`) than on Cloud Run.
- **Custom images** start `FROM ghcr.io/tosun-si/duckless-runner:<version>` to keep
  `duckless_runtime` and the extensions; the runner service account needs
  `roles/artifactregistry.reader` on the image's repository.
- Reading many small files is slower than few large ones: compact upstream when you can.

Docs: https://tosun-si.github.io/duckless/guides/writing-jobs/
