---
title: Writing jobs
description: SQL files, Python scripts and your own images, and the few habits that make them fast.
---

A job is one of three things:

| You give | DuckLess runs | Command |
| --- | --- | --- |
| a `.sql` file | every statement, in order, on the runner | `duckless run job.sql` |
| a `.py` file | the script, with `duckless_runtime` importable | `duckless run job.py` |
| an image and a command | your command, in your image | `duckless exec --image … -- <command>` |

In all three cases the job runs on its own VM, with the service account created by
`duckless init`. That account can read and write the work bucket, and the buckets you
passed to `init --data-bucket`.

## SQL jobs

The file is split into statements and run top to bottom. `${VAR}` placeholders are replaced
with the job's environment before running, which gives you the work bucket and anything
passed with `--env`:

```sql
CREATE VIEW orders AS
SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/raw/orders/*.parquet');

COPY (
    SELECT customer_id, date_trunc('month', ordered_at) AS month, sum(amount) AS revenue
    FROM orders
    WHERE ordered_at >= DATE '${SINCE}'
    GROUP BY ALL
) TO 'gs://${DUCKLESS_BUCKET}/marts/revenue_by_month'
  (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB', OVERWRITE);

SELECT count(*) FROM orders;
```

```bash
duckless run revenue.sql -m n2-highmem-16 --spot -e SINCE=2026-01-01
```

`$1`-style parameters are left alone. The rows of the last `SELECT` (up to 20) are kept with
the job's metrics and shown by `duckless result <job-id>`.

`gs://` paths work everywhere DuckDB expects a file: `read_parquet`, `read_csv`, `glob`,
`COPY … TO`, partitioned writes.

## Python jobs

The runner image ships the `duckless_runtime` package. `connect()` returns a DuckDB
connection that is already set up for the VM it runs on: GCS through the VM's credentials,
spill directory on local SSD, memory limit and threads sized to the machine.

```python
import os

from duckless_runtime import connect, copy_to_parquet, log

bucket = os.environ["DUCKLESS_BUCKET"]
con = connect()

orders = con.sql(f"SELECT * FROM read_parquet('gs://{bucket}/raw/orders/*.parquet')")
copy_to_parquet(con, "SELECT * FROM orders WHERE amount > 0", f"gs://{bucket}/clean/orders")

log("orders_cleaned", rows=con.sql("SELECT count(*) FROM orders").fetchone()[0])
```

`log()` writes one JSON line on stdout; it shows up as a structured entry in Cloud Logging
and in `duckless logs`.

### Keep Python logic vectorized

A row-by-row Python UDF runs on a single thread at around 8,000 rows per second. On ten
million rows that is twenty minutes, whatever the size of the machine. Write the rule so it
works on whole arrays and register it as an Arrow UDF:

```python
import numpy as np
import pyarrow as pa

def segment(revenue: np.ndarray, orders: np.ndarray) -> np.ndarray:
    return np.select(
        [revenue > 2_000_000, revenue / np.maximum(orders, 1) > 150_000],
        ["key_account", "big_basket"],
        default="regular",
    )

def segment_arrow(revenue: pa.Array, orders: pa.Array) -> pa.Array:
    return pa.array(segment(revenue.to_numpy(zero_copy_only=False), orders.to_numpy(zero_copy_only=False)))

con.create_function("segment", segment_arrow, ["DOUBLE", "BIGINT"], "VARCHAR", type="arrow")
```

DuckDB calls it on batches of about 2,000 rows, in parallel. The same ten million rows take
a few seconds. The plain `segment` function stays easy to unit test with small arrays.

## Your own image

`duckless exec` runs any command in any image. The command replaces the image entrypoint:

```bash
duckless exec --image europe-docker.pkg.dev/acme/jobs/dbt:1.4 -m n2-highmem-32 --spot \
  -- dbt build --target duckless
```

To get `duckless_runtime` and the DuckDB extensions in your image, build it `FROM` the
runner image:

```dockerfile
FROM ghcr.io/tosun-si/duckless-runner:0.1.0
COPY --chown=app:app my_project/ ./my_project/
```

## Writing to GCS fast

DuckDB writes a single file sequentially by default. To GCS that caps around 110 MB/s,
however big the VM. Let every thread write its own files:

```sql
COPY my_table TO 'gs://bucket/path' (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB');
```

On 32 vCPUs this goes to about 900 MB/s, close to local SSD speed. `copy_to_parquet()` in
Python uses these options. Partitioned writes (`PARTITION_BY`) are limited by how many
partitions are written at the same time.
