# 05 · BigQuery to Python

Data already in BigQuery, business logic in Python: read the table from a job, transform it, write
the result. The read goes through the BigQuery Storage Read API: only the columns and rows the job
asks for leave BigQuery, in parallel streams, billed at Storage Read API prices (about six times
less than an on-demand query on the same data).

## Setup, once

Put the orders of [`00-seed`](../00-seed/) in a BigQuery dataset of your project, in the region of
your jobs (a load job is free), and let the jobs read it:

```bash
bq --location=europe-west1 mk --dataset $DUCKLESS_PROJECT:duckless_examples
bq --location=europe-west1 load --source_format=PARQUET \
  duckless_examples.orders "gs://$DUCKLESS_BUCKET/examples/raw/orders/*.parquet"

duckless init --project $DUCKLESS_PROJECT --bigquery-dataset duckless_examples
```

## SQL: `bigquery_scan`

```bash
duckless run examples/05-bigquery-to-python/top_customers.sql -m n2-standard-4 -e BQ_DATASET=duckless_examples
duckless result <job-id>
```

```sql
SELECT o_custkey, sum(o_totalprice)
FROM bigquery_scan('my-project.duckless_examples.orders', filter = 'o_orderdate >= DATE ''1997-01-01''')
GROUP BY ALL;
```

The `filter` is applied by BigQuery, in BigQuery SQL; the columns the query uses are the only ones
read.

## Python: `read_bigquery` and a vectorized function

```bash
duckless run examples/05-bigquery-to-python/score_orders.py -m n2-highmem-16 --on batch -e BQ_DATASET=duckless_examples
```

```python
orders = read_bigquery(con, table, columns=[...], where="o_orderdate >= DATE '1995-01-01'")
orders.create_view("orders")
con.create_function("order_score", order_score, [DOUBLE, VARCHAR], DOUBLE, type="arrow")
copy_to_parquet(con, "SELECT ..., order_score(o_totalprice, o_orderpriority) AS score FROM orders", out)
```

`order_score` is plain NumPy over Arrow arrays: DuckDB calls it with thousands of rows at a time,
on every thread. A row-by-row Python function would be hundreds of times slower. It is tested on
its own, without BigQuery (`test_score_orders.py`).

`read_bigquery` returns a DuckDB relation: query it in SQL as here, or get Arrow batches
(`fetch_record_batch()`), pandas or Polars from it.

## Writing back to BigQuery

Load the Parquet the job wrote (a load job is free):

```bash
bq --location=europe-west1 load --source_format=PARQUET --replace \
  duckless_examples.scored_orders "gs://$DUCKLESS_BUCKET/examples/bigquery/scored_orders/*.parquet"
```

## Good to know

- **Big tables: run on Cloud Batch.** Reading 60 million rows took 16 s on `n2-highmem-16` and
  95 s on an 8 vCPU Cloud Run job: use `--on batch` (or a machine too big for Cloud Run) when the
  read is large.
- **Tables of other projects** can be read the same way; their owners grant the runner account
  `roles/bigquery.dataViewer`. `init` grants datasets of its own project only.
- **Queries** (`bigquery_query`, `ATTACH … (TYPE bigquery)`) need `duckless init --bigquery-jobs`
  and are billed as BigQuery queries; `bigquery_scan` is enough to read tables.

Clean up: `bq rm -r -f -d $DUCKLESS_PROJECT:duckless_examples`.
