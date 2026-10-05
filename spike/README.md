# DuckLess spike

Question: can a Cloud Batch VM run DuckDB on large Parquet in GCS, auth via ADC only (no HMAC),
with spill on local SSD, fast enough and cheap enough to matter?

The spike validated the approach (verdict below) and its code became the product: its runner
is now `runtime/`, its submit script `duckless run`, its setup script `duckless init`. What is
left here are the jobs and the BigQuery benchmark, to reproduce the numbers.

## Pieces

| Path | What |
| --- | --- |
| `jobs/tpch_gen.sql` | Generates TPC-H at `TPCH_SF` and writes Parquet to the work bucket |
| `jobs/tpch_queries.sql` | Full scan + Q1/Q9/Q18/Q21 straight from GCS, writes an aggregate back |
| `jobs/write_bench.sql` | GCS write throughput: single writer vs `PER_THREAD_OUTPUT`, partitioned |
| `jobs/customer_segments.py` | A "pure-Python Cloud Run job" rewritten on DuckLess: SQL for the heavy part, a vectorized Python rule |
| `jobs/customer_segments_rowwise.py` | Same job with a row-wise Python UDF, kept to show why not to |
| `bq_bench.py` | The same TPC-H queries on BigQuery on-demand, external or native tables |

## Reproduce

```bash
duckless init --project <project> --region europe-west1   # then add the printed lines to .envrc

duckless run spike/jobs/tpch_gen.sql     -m n2-highmem-32 --spot -e TPCH_SF=100
duckless run spike/jobs/tpch_queries.sql -m n2-highmem-32 --spot -e TPCH_SF=100
duckless run spike/jobs/tpch_queries.sql -m n2-highmem-32 --spot -e TPCH_SF=100 -e DUCKLESS_MEMORY_FRACTION=0.03
duckless run spike/jobs/write_bench.sql  -m n2-highmem-32 --spot -e TPCH_SF=100
duckless run spike/jobs/customer_segments.py -m n2-highmem-16 --spot -e TPCH_SF=100

uv run spike/bq_bench.py --sf 100 --mode external   # and --mode native
```

`dbgen` is mostly single-threaded: generating SF100 takes ~20 min whatever the machine.

## Findings (2026-09-30 and 2026-10-01, europe-west1)

**Verdict: go.** ADC-only GCS access works end to end on Batch VMs (metadata server, no HMAC), spill lands on local SSD,
and on TPC-H SF100 DuckLess is as fast as BigQuery for ~20-80x less per run.

### TPC-H SF100 (35.6 GB Parquet on GCS, read in place)

| Engine | Queries (scan + Q1/Q9/Q18/Q21 + write) | Startup | Cost per run |
| --- | --- | --- | --- |
| DuckLess n2-highmem-32 Spot, 4 local SSD | 47 s | 58 s | ~0.02-0.08 USD (estimate, to confirm with billing) |
| DuckLess, memory_limit 25 GB | 66 s | 80 s | idem |
| DuckLess, memory_limit 7 GB | 133 s, 0.9 GB spilled, no failure | 73 s | idem |
| BigQuery on-demand, external tables | 59 s | - | 1.78 USD |
| BigQuery on-demand, native tables | 39 s | - | 1.53 USD (+ storage) |

- lineitem full scan (600 M rows, ~23 GB Parquet, all columns): 17.5 s = ~1.3 GB/s from GCS on 32 vCPU.
- BigQuery bills ~105 GB logical for that scan (3-4.5x the Parquet size).
- GCS write: a single writer (`FILE_SIZE_BYTES` only) uploads at ~110 MB/s. See the write benchmark below.

### GCS write throughput (jobs/write_bench.sql, lineitem SF100 = 22.8 GB Parquet, n2-highmem-32)

| Variant | HTTP | gRPC | Files |
| --- | --- | --- | --- |
| Local SSD, FILE_SIZE_BYTES 256MB (encoding only) | 739 MB/s | 764 MB/s | - |
| Local SSD, PER_THREAD_OUTPUT (encoding only) | 1051 MB/s | 1041 MB/s | - |
| GCS, FILE_SIZE_BYTES 256MB | 110 MB/s | 168 MB/s | 60-73 |
| GCS, PER_THREAD_OUTPUT | 797 MB/s | 880 MB/s | 32 x ~700 MB |
| GCS, PER_THREAD_OUTPUT + FILE_SIZE_BYTES 256MB | 793 MB/s | 900 MB/s | 96 x ~240 MB |
| GCS, PARTITION_BY (year), 7 partitions | 346 MB/s | 335 MB/s | 7 |

- The bottleneck was the single sequential writer, not GCS: PER_THREAD_OUTPUT is 7-8x faster and close to local SSD speed (encoding-bound).
- gRPC (`SET gcs_enable_grpc = true`, before the first GCS call) helps a bit everywhere: +50 % single writer, +10 % per-thread, load 38 s -> 29 s. Now the runner default.
- Recommended write: `(FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB')`. Partitioned writes are capped by the number of partitions written concurrently.

### Pure-Python job (customer_segments.py, SF100: 600 M x 150 M join, ~10 M customers)

| Where | Run | Wall clock incl. startup | Peak RSS |
| --- | --- | --- | --- |
| DuckLess n2-highmem-16 Spot | 44 s | ~1 min 50 | 25 GB |
| Cloud Run Jobs 8 vCPU / 32 GiB | 247 s | ~7 min (3 min provisioning) | 24.6 GB, i.e. at the edge of the 32 GiB cap |

- Row-wise Python UDF: ~8k rows/s single-threaded, 20+ min at SF100 on both platforms -> vectorized (Arrow/numpy) UDF or SQL is mandatory. Worth a doc page / lint in the SDK.

### Integration lessons (feed the CLI)

- Batch accepts a single lifecycle policy per task.
- `no_external_ip_address` needs both network and subnetwork.
- Local SSD count is constrained per machine (n2-highmem-16: 0/2/4/8/16/24) and Batch only fails after ~25 s -> validate client-side.
- PREEMPTIBLE_CPUS quota at 0 is fine: Spot falls back to the standard CPU quota.
- httpfs must not be installed in the image, or it takes over gs://; secret is `TYPE GCP, PROVIDER credential_chain`.
- A Parquet file written by another tool (public cloud-samples-data) failed with DuckDB prefetch (bad page offsets): check with real client files.
- `os.cpu_count()` over-reports on Cloud Run (9-10 for 8 vCPU): read the cgroup CPU quota.
- JSON lines on stdout land as structured `jsonPayload` in Cloud Logging from Batch and Cloud Run.
