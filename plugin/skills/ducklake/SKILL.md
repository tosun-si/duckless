---
name: ducklake
description: Use DuckLake tables with DuckLess - a Cloud SQL catalog attached as `lake` in every job, data as Parquet on GCS, snapshots and time travel. Use when someone wants tables instead of loose Parquet files, incremental loads, updates/deletes, time travel, or when a DuckLake attach fails.
---

# DuckLake with DuckLess

DuckLake turns Parquet files on GCS into tables with snapshots. DuckLess keeps the catalog in
a Cloud SQL Postgres instance and attaches it as `lake` in every job, on both runners.

## Turn it on

```bash
duckless init --project <project> --ducklake
```

Then add the two extra lines `init` prints to `.envrc`:
`DUCKLESS_DUCKLAKE_INSTANCE` (connection name) and `DUCKLESS_DUCKLAKE_DATA_PATH`
(`gcss://<work bucket>/lake/`). Without them, jobs do not attach the catalog.

Cost: a `db-g1-small` instance (about $25/month), backups and 7 days of point-in-time recovery
included.

## Use it

```sql
CREATE TABLE lake.orders AS SELECT * FROM read_parquet('gs://data/orders/*.parquet');
INSERT INTO lake.orders SELECT * FROM read_parquet('gs://data/orders_today/*.parquet');
UPDATE lake.orders SET status = 'refunded' WHERE order_id IN (SELECT order_id FROM refunds);

SELECT * FROM ducklake_snapshots('lake');            -- history
SELECT count(*) FROM lake.orders AT (VERSION => 3);  -- time travel: a literal, no subquery
```

Python: `duckless_runtime.connect()` returns a connection with `lake` attached.

## Rules

- Several jobs can write at once; each commit is a snapshot. Keep writes in a few large
  statements rather than many tiny ones (each is a snapshot and small files).
- `AT (VERSION => …)` takes a literal; read the version from `ducklake_snapshots` first.
- The data path is `gcss://`, never `gs://` (DuckLake would hand `gs://` to httpfs).
- Never put a password in an `ATTACH`: the runner connects through the Cloud SQL Auth Proxy
  with IAM; there is no password. DuckDB prints DSNs in errors.
- The catalog is the lake: losing it leaves Parquet files without table definitions. Do not
  `destroy --force` a lake installation without the user's explicit go.
- Maintenance (compaction, expiring snapshots, removing old files) is done with DuckLake's own
  functions; check their names for the DuckLake version of the runner image before running
  them, and run them as a normal DuckLess job.

## When the attach fails

1. `.envrc` has both `DUCKLESS_DUCKLAKE_*` lines and they match `init`'s output.
2. The job's network is the catalog's network (`--network` given to `init`).
3. Logs: `ducklake_attached` is printed on success; a `cloud-sql-proxy` error names the cause
   (permissions: the runner account needs `cloudsql.client` and `cloudsql.instanceUser`, set by `init`).

Docs: https://tosun-si.github.io/duckless/guides/ducklake/
