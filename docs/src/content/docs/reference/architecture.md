---
title: Architecture
description: How a job runs, and how the code is organised.
---

## How a job runs

```
your laptop / CI                      your GCP project
────────────────                      ──────────────────────────────────────────────
duckless run job.sql ──┬─ upload ───▶ work bucket   gs://<project>-duckless-work/runs/<job>/
                       └─ submit ───▶ Cloud Batch
                                         │ creates
                                         ▼
                                      VM (n2-highmem-…, local SSD, no external IP)
                                         │ pulls runner image ◀── Artifact Registry (remote repo)
                                         │                          ◀── ghcr.io/tosun-si/duckless-runner
                                         │ DuckDB reads / writes gs:// with the VM's service account
                                         │ logs ───────────────▶ Cloud Logging
                                         │ metrics ────────────▶ work bucket
                                         ▼
                                      VM deleted when the job ends
```

1. The CLI validates the job (machine, local SSD count), uploads the source to the work
   bucket and submits a Batch job.
2. Batch starts a VM in any zone of the region, attaches the local SSDs and pulls the
   runner image through the project's Artifact Registry remote repository.
3. The runner opens DuckDB with the `gcs` extension (credentials from the metadata
   server, gRPC transport), spill on local SSD, memory and threads sized to the VM, then
   runs the job.
4. It logs JSON lines to Cloud Logging and writes its metrics to the work bucket.
5. Batch deletes the VM. `duckless status` reads the job state and the metrics.

## Code layout

The CLI is a small hexagonal application:

| Path | |
| --- | --- |
| `duckless/core/` | rules with no I/O: machine types and local SSD counts, job planning, quotas, preflight, infra requests |
| `duckless/ports.py` | what the service needs from outside, as `Protocol`s: `Executor`, `ArtifactStore`, `LogReader`, `QuotaReader`, `InfraBootstrap`, `InfraDeployer` |
| `duckless/service.py` | the operations (`run_job`, `get_job`, `init_infra`, …), plain functions taking ports as arguments |
| `duckless/adapters/` | Google Cloud implementations: Batch, GCS, Cloud Logging, Compute quotas, Infrastructure Manager |
| `duckless/wiring.py` | binds the service functions to the adapters |
| `duckless/cli.py` | the `duckless` command |
| `duckless/terraform/` | the module applied by `init` |
| `runtime/` | the runner image and its `duckless_runtime` package |

`core` imports nothing else from DuckLess; `service` only imports `core` and `ports`; only
`wiring` knows the adapters. Tests use in-memory fakes of the ports, no Google Cloud.
