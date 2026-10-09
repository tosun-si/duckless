---
title: Reading BigQuery
description: Read BigQuery tables from jobs, in SQL or Python, through the Storage Read API.
---

Jobs read BigQuery tables directly, with DuckDB's `bigquery` extension (baked into the runner):
the BigQuery Storage Read API streams the table as Arrow, in as many parallel streams as the job
has threads. Only the columns the job uses and the rows its filter keeps leave BigQuery.

## Turn it on

```bash
duckless init --project my-project --bigquery-dataset sales --bigquery-dataset ref
```

The runner account gets `roles/bigquery.readSessionUser` on the project and
`roles/bigquery.dataViewer` on each dataset. `init` keeps them on later runs; `--bigquery-dataset`
again replaces the list.

Datasets of other projects: their owners grant the runner account (`DUCKLESS_SA`)
`roles/bigquery.dataViewer`. Infrastructure Manager only acts in the installation's project.

## SQL

```sql
SELECT customer_id, sum(amount) AS revenue
FROM bigquery_scan('my-project.sales.orders', filter = 'order_date >= DATE ''2026-01-01''')
GROUP BY ALL;
```

`filter` is BigQuery SQL, applied by BigQuery. Nothing to `LOAD`: the runner loads the extension.

## Python

```python
from duckless_runtime import connect, read_bigquery

con = connect()
orders = read_bigquery(con, "my-project.sales.orders", ["order_id", "amount"], "order_date >= DATE '2026-01-01'")

orders.create_view("orders")  # then SQL, Arrow UDFs...
for batch in orders.fetch_record_batch():  # ...or Arrow batches, never the whole table in memory
    ...
df = orders.pl()  # ...or Polars (orders.df() for pandas), when it fits
```

[Example 05](https://github.com/tosun-si/duckless/tree/main/examples/05-bigquery-to-python) reads
a table, scores it with a vectorized NumPy function and writes the result to GCS.

## How fast, and what it costs

Measured in `europe-west1` on TPC-H `lineitem` at scale 10 (60 million rows, 10.5 GB in BigQuery):

| Read | `n2-highmem-16` (Cloud Batch) | 8 vCPU (Cloud Run Jobs) |
| --- | --- | --- |
| 2 columns, aggregated | 1.8 s | 6.8 s |
| the whole table, written to GCS as Parquet | 15.7 s | 95 s |

Reading large tables is much faster on Cloud Batch: use `--on batch` for them. Small tables are
fine on Cloud Run.

The Storage Read API is billed per byte read, about six times less than an on-demand query over
the same data: reading the whole table above cost about one US cent.

## Queries

To run SQL inside BigQuery (`bigquery_query`), or to `ATTACH` a project and see its datasets as
schemas, add `--bigquery-jobs` to `init` (`roles/bigquery.jobUser`). Those are BigQuery queries,
billed as such; `bigquery_scan` is enough to read tables.

## Writing back

Write Parquet to GCS, then load it into BigQuery: a load job is free.

```bash
bq load --source_format=PARQUET --replace sales.orders_scored "gs://my-bucket/out/*.parquet"
```
