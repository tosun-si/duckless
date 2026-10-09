---
title: Examples
description: Pipelines you can run as they are, from generated data to marts and DuckLake tables.
---

The [`examples/`](https://github.com/tosun-si/duckless/tree/main/examples) folder of the
repository holds pipelines you can run as they are. The first one generates TPC-H data on the
runner, so nothing needs downloading.

| Example | What it shows | Command |
| --- | --- | --- |
| [Seed](https://github.com/tosun-si/duckless/tree/main/examples/00-seed) | TPC-H generated on the runner, written to the work bucket | `duckless run examples/00-seed/seed.sql -m n2-standard-4 -e SCALE=1` |
| [Daily marts](https://github.com/tosun-si/duckless/tree/main/examples/01-daily-marts) | joins, a partitioned write and a parallel write, in plain SQL | `duckless run examples/01-daily-marts/marts.sql -m n2-standard-8` |
| [Late corrections with DuckLake](https://github.com/tosun-si/duckless/tree/main/examples/02-ducklake-incremental) | CSV drops into a table, JSON corrections applied with `UPDATE` and `DELETE`, snapshots, time travel | `duckless run examples/02-ducklake-incremental/load_day.sql -m n2-standard-4 -e LOAD_DATE=1998-07-01` |
| [Raw data validation](https://github.com/tosun-si/duckless/tree/main/examples/03-raw-validation) | YAML rules checked by Pydantic, data checked by DuckDB (100 million lines in about a minute), errors reported to GCS, job failing above a threshold | `duckless run examples/03-raw-validation/validate.py -m n2-standard-8 -e RULES=gs://…/rules.yaml` |
| [BigQuery to Python](https://github.com/tosun-si/duckless/tree/main/examples/05-bigquery-to-python) | read a BigQuery table in SQL and Python, vectorized NumPy logic, result to GCS | `duckless run examples/05-bigquery-to-python/score_orders.py -m n2-highmem-16 --on batch -e BQ_DATASET=…` |

Run them from the repository root, in an installation made with `duckless init` (`--ducklake`
for the third). Everything they write goes under `gs://$DUCKLESS_BUCKET/examples/`.

## Patterns worth copying

- **Re-runnable jobs.** `COPY … (OVERWRITE)` for files, delete-then-insert in one transaction
  for DuckLake tables: a scheduler can retry a day without duplicating it.
- **Parameters through the environment.** `${LOAD_DATE}` in the SQL, `-e LOAD_DATE=…` on the
  command line.
- **A check at the end.** The last `SELECT` is what `duckless result` shows: make it a small
  summary of what the job just wrote.
