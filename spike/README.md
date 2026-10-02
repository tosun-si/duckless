# DuckLess spike

Question: can a Cloud Batch VM run DuckDB on large Parquet in GCS, auth via ADC only (no HMAC),
with spill on local SSD, fast enough and cheap enough to matter?

## Pieces

| Path | What |
| --- | --- |
| `runner/` | Runner image: `duckless_runtime.connect()` = DuckDB + `gcs` community extension (ADC) + spill on local SSD + memory/threads sized to the VM. `python -m duckless_runtime sql|py <uri>` |
| `submit.py` | Submits a job to Batch (machine, Spot, local SSD, no external IP), follows it, writes `results/<job-id>.json` (Batch timeline + runner metrics) |
| `setup.sh` | Spike infra: Batch API, bucket, AR repo, runner SA + least-privilege roles |
| `jobs/tpch_gen.sql` | Generates TPC-H at `TPCH_SF` and writes Parquet to GCS |
| `jobs/tpch_queries.sql` | Full scan + Q1/Q9/Q18/Q21 straight from GCS, writes an aggregate back |
| `jobs/customer_segments.py` | A "pure-Python Cloud Run job" rewritten on DuckLess: SQL for the heavy part, Python for the business rule |

## Run

```bash
cp .envrc.example .envrc && direnv allow
./setup.sh

docker buildx build --platform linux/amd64 -t "$DUCKLESS_IMAGE" --push runner/

uv run submit.py jobs/tpch_gen.sql     --machine n2-highmem-16 --local-ssd 2 --spot --env TPCH_SF=10
uv run submit.py jobs/tpch_queries.sql --machine n2-highmem-32 --local-ssd 4 --spot --env TPCH_SF=100
uv run submit.py jobs/customer_segments.py --machine n2-highmem-16 --local-ssd 2 --spot --env TPCH_SF=100
```

## Findings (2026-09-30, europe-west1, gb-poc-373711)

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
