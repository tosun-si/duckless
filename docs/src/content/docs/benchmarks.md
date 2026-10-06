---
title: Benchmarks
description: TPC-H SF100 against BigQuery, a Cloud Run job, spill and GCS write throughput.
---

All numbers come from single runs in `europe-west1`, between 30 September and 5 October
2026, with Parquet files written by DuckDB itself. Treat them as orders of magnitude, not
as a lab benchmark. The jobs to reproduce them are in
[`spike/`](https://github.com/tosun-si/duckless/tree/main/spike).

## TPC-H SF100 against BigQuery

35.6 GB of Parquet on GCS (600 million rows in `lineitem`), read in place. The workload: a
full scan of `lineitem`, TPC-H queries 1, 9, 18 and 21, and an aggregate written back to
GCS.

| Engine | Query time | Startup | Cost per run |
| --- | --- | --- | --- |
| DuckLess, `n2-highmem-32` Spot | 47 s | 58 s | ≈ 0.02 to 0.08 $ (estimate) |
| BigQuery on-demand, external tables | 59 s | none | 1.78 $ |
| BigQuery on-demand, native tables | 39 s | none | 1.53 $ (+ storage) |

The full scan of `lineitem` reads at about 1.3 GB/s on 32 vCPUs. BigQuery bills around
105 GB of logical bytes for that same scan, three to four times the size of the Parquet
files.

What this means in practice: for one-off queries that must answer fast, BigQuery wins on
latency. For transformations that run every day and take minutes, DuckLess costs a small
fraction of the price for the same compute time.

## Spill

The same SF100 queries with less memory given to DuckDB:

| Memory limit | Query time | Spilled to local SSD |
| --- | --- | --- |
| 205 GB (default on `n2-highmem-32`) | 47 s | none |
| 25 GB | 66 s | none |
| 7 GB | 133 s | 0.9 GB |

Nothing fails; it gets slower.

## A Python job: Cloud Run against DuckLess

A job written the way many teams write Cloud Run jobs: join `lineitem` with `orders`
(600 million × 150 million rows), aggregate per customer, apply a business rule in Python
to about 10 million customers, write the result to GCS.

| Where | Run | Total with startup | Peak memory |
| --- | --- | --- | --- |
| DuckLess, `n2-highmem-16` Spot | 44 s | about 1 min 50 | 25 GB |
| Cloud Run job, 8 vCPU / 32 GiB | 247 s | about 7 min | 24.6 GB, at the limit |

The Cloud Run job finishes, but it is five times slower and a bigger input would not fit.

The first version of this job used a row-by-row Python UDF: more than 20 minutes on both
platforms. See [keeping Python vectorized](/duckless/guides/writing-jobs/#keep-python-logic-vectorized).

## Writing to GCS

`lineitem` at SF100 (22.8 GB of Parquet) written from memory on `n2-highmem-32`:

| Write | HTTP | gRPC |
| --- | --- | --- |
| One writer, `FILE_SIZE_BYTES '256MB'` | 110 MB/s | 168 MB/s |
| `PER_THREAD_OUTPUT` | 797 MB/s | 880 MB/s |
| `PER_THREAD_OUTPUT` + `FILE_SIZE_BYTES '256MB'` | 793 MB/s | 900 MB/s |
| `PARTITION_BY` year (7 partitions) | 346 MB/s | 335 MB/s |
| To local SSD, `PER_THREAD_OUTPUT` (reference) | 1,051 MB/s | |

The limit was the single sequential writer, not GCS. The runner uses gRPC by default.
