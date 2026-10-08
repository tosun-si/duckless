# 00 · Seed

Generates [TPC-H](https://www.tpc.org/tpch/) data with DuckDB's `tpch` extension, on the
runner, and writes it to the work bucket. No data to download, nothing private.

```bash
duckless run examples/00-seed/seed.sql -m n2-standard-4 -e SCALE=1
```

| Written to `gs://$DUCKLESS_BUCKET/examples/raw/` | Rows at `SCALE=1` |
| --- | --- |
| `nation/`, `customer/`, `orders/`, `lineitem/` | 25, 150 thousand, 1.5 million, 6 million |
| `orders_daily/o_orderdate=1998-07-01/` … `=1998-07-07/` | one folder of CSV per day, the input of example 02 |
| `corrections/1998-07-06.ndjson` | cancelled orders and an erased customer, as newline-delimited JSON |

`SCALE` is the TPC-H scale factor: `SCALE=1` is about 1 GB of raw data and fits a small
machine; `SCALE=100` (about 100 GB) wants `-m n2-highmem-32 --spot`.

The big tables are written with `PER_THREAD_OUTPUT`: one file per thread, several times faster
to GCS than a single writer.
