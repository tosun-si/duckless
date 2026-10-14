---
title: Writing to BigQuery from DuckDB
description: "Write a DuckDB result to BigQuery: a free load job, then replace partitions or a pruned MERGE that bills only the data written, never the table."
---

A job transforms with DuckDB, then writes the result to BigQuery with one call:

```python
from duckless_runtime import connect, to_bigquery

con = connect()
con.sql("CREATE TABLE accepted AS SELECT ...")  # the transformations, in DuckDB

to_bigquery(con, "SELECT * FROM accepted", "my-project.finance.journal_lines", mode="replace_partitions")
```

Under the hood: the result is written as Parquet to the work bucket, one file per thread, then a
**load job** brings it into BigQuery. A load job is free, atomic, and reads the files in parallel:
it is BigQuery's own bulk path. What happens after the load depends on the mode.

## Turn it on

```bash
duckless init --project my-project --bigquery-write-dataset finance
```

With the Terraform module: `bigquery_write_datasets = ["finance"]`. The runner account gets
`roles/bigquery.dataEditor` on each dataset listed, and `roles/bigquery.jobUser` on the project for
the load jobs and the SQL after them. It can't write anywhere else. Datasets of other projects:
their owners grant the runner account (`DUCKLESS_SA`) `roles/bigquery.dataEditor`.

## Modes

| Mode | What it does | BigQuery bills | Table must exist |
| --- | --- | --- | --- |
| `append` | load job, adds the rows | nothing | no: created |
| `replace` | load job, replaces the whole table (its partitioning and clustering stay) | nothing | no: created |
| `replace_partitions` | load into a staging table, then one transaction: delete the partitions the data touches, insert the rows | 10 MB + the new rows | yes |
| `merge` | load into a staging table, then one `MERGE` on `key` | 10 MB + the new rows + the target partitions read | yes |

Every mode is atomic: the table shows all of the write or none of it, never half a month.

`append` and `replace` create a missing table; `partition_by` and `cluster_by` set it up:

```python
to_bigquery(
    con,
    "SELECT * FROM lines",
    "my-project.finance.journal_lines",
    mode="append",
    partition_by="posting_date",
    cluster_by=["entry_id", "line_no"],
)
```

## Choosing a mode: replace partitions when you can

Measured on a 100-million-row table (4.8 GB, one partition per day), with a batch of 3 full days
(822,000 rows):

| Strategy | Billed | Seconds |
| --- | ---: | ---: |
| `MERGE` on the key, nothing else (table not clustered) | 4.81 GB: the whole table | 2.1 |
| same, table clustered on the key | 1.68 GB | 2.7 |
| `MERGE` reading only the touched partitions (`merge`, pruned) | 0.09 GB | 4.8 |
| delete the touched partitions + insert (`replace_partitions`) | 0.05 GB | 3.6 |
| `DELETE` of whole partitions, alone | 0 | 0.6 |

What a write costs depends on the data written, not on the size of the table: on a 2 TB table the
plain `MERGE` bills 2 TB on every run, the two others still bill megabytes.

- **`replace_partitions`** is the cheapest: deleting whole partitions is free (metadata only), the
  insert reads the new rows. Use it when **each batch holds the complete content of every
  partition it touches**: a month-end close rewrites its month, a daily load its day.
- **`merge`** when a batch holds only some rows of a partition: corrections, late rows.

### When replace_partitions is wrong

It replaces **whole partitions**. A batch with 3 days of a table partitioned by **month** deletes
the rest of the month. A table partitioned by **load date** (a technical date, or
`_PARTITIONTIME`) does not group the rows a batch rewrites: replacing those partitions would delete
other rows, or miss the old ones. DuckLess refuses ingestion-time tables, but only you know whether
a batch is complete for its partitions: when in doubt, `merge`.

### Making merge cheap

```python
to_bigquery(
    con,
    "SELECT * FROM corrections",
    "my-project.finance.journal_lines",
    mode="merge",
    key=["posting_date", "entry_id", "line_no"],
)
```

- **The partition column in `key`**: the `MERGE` reads only the partitions the data touches.
  DuckLess computes them first into a script variable: BigQuery refuses a subquery in a `MERGE`'s
  `ON` clause ("IN subquery is not supported inside join predicate"), so hand-written merges
  often drop the filter and read the whole table.
- **Not in the key, but a row never changes partition** (an entry keeps its posting date):
  `prune=True` gives the same pruning. Wrong if a row can move: its old version, in another
  partition, would not be found and the row would be inserted twice. That's why it isn't the
  default; without pruning the job logs `bigquery_merge_not_pruned`.
- **Cluster the table on the key**: the `MERGE` reads fewer blocks of each partition.

For SCD2 history, snapshots or per-column rules, load into a table with `replace_partitions` or
`append` and let dbt (or SQL in BigQuery) take it from there: DuckLess keeps to four plain modes.

## Guards

Before anything reaches the table:

- **Columns**: data columns missing from the table are refused, by name. The staging table takes the
  table's column types: a value that doesn't convert fails the load, before the table is touched.
- **merge**: two rows with the same key (BigQuery would refuse the `MERGE`) and NULL keys (they would
  never match and be inserted again on every run) are refused, counted by DuckDB over the files.
- **replace_partitions**: rows with a NULL partition column are refused (no partition would be
  replaced for them); the table must be partitioned by a column.
- **replace with no rows** is refused: it would empty the table, and an empty result is most often an
  upstream bug (a missing input file). `allow_empty=True` when emptying it is the intent.
- **`max_bytes_billed`**: a cap on what the SQL step may bill; beyond it BigQuery refuses the query
  and the table stays as it was.

```python
to_bigquery(con, query, table, mode="merge", key=["order_id"], max_bytes_billed=50 * 1024**3)
```

## What's left behind: nothing

The staging table lives in the table's dataset (`_duckless_stage_…`, hence `dataEditor` there),
expires after a day and is dropped at the end, even on an error. The Parquet files go under the job's
folder of the work bucket, `runs/<job>/bigquery/`, are deleted at the end, and the bucket's lifecycle
rule removes them if the VM was lost mid-write.

## Retries and Spot

A Spot VM taken back is run again from the start. `replace`, `replace_partitions` and `merge` give
the same table when run twice; `append` adds the rows a second time if the first run had loaded them.
On Spot, prefer one of the first three.

## Types

| DuckDB | BigQuery |
| --- | --- |
| `DECIMAL(p, s)`, s ≤ 9 and p − s ≤ 29 | `NUMERIC` |
| `BIGINT`, `INTEGER` | `INT64` |
| `DOUBLE` | `FLOAT64` |
| `VARCHAR` | `STRING` |
| `DATE` | `DATE` |
| `TIMESTAMP WITH TIME ZONE` | `TIMESTAMP` |
| `TIMESTAMP` (no time zone) | `TIMESTAMP`: the value is read as UTC |

These apply when `append` or `replace` creates the table; otherwise the table's own types win.
In DuckDB, dividing a `DECIMAL` gives a `DOUBLE`: cast back (`CAST(x / 100 AS DECIMAL(18, 2))`) to
keep `NUMERIC` amounts.

## Writing outside a DuckLess job

On a laptop, `to_bigquery` needs a bucket for the Parquet files:
`to_bigquery(..., staging_uri="gs://my-bucket/tmp")`.
