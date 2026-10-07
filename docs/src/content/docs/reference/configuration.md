---
title: Configuration
description: Environment variables read by the CLI and by the runner.
---

## CLI

`duckless init` prints the first four. Keep them in a `.envrc` with
[direnv](https://direnv.net/), or export them in your shell.

| Variable | Required | |
| --- | --- | --- |
| `DUCKLESS_PROJECT` | yes | project where jobs run |
| `DUCKLESS_REGION` | no | region of the jobs, default `europe-west1` |
| `DUCKLESS_BUCKET` | yes | work bucket |
| `DUCKLESS_SA` | yes | service account of the job VMs |
| `DUCKLESS_IMAGE` | yes | runner image used by `run` |
| `DUCKLESS_NETWORK` | no | network of the job VMs, default `default` |
| `DUCKLESS_SUBNETWORK` | no | subnetwork, default `default`; a full path for Shared VPC |
| `DUCKLESS_EXTERNAL_IP` | no | `true` to give job VMs an external IP (not needed with Private Google Access) |

## Runner

Set for every job, readable from SQL (`${VAR}`) and Python (`os.environ`):

| Variable | |
| --- | --- |
| `DUCKLESS_JOB_ID` | the job id |
| `DUCKLESS_BUCKET` | the work bucket |
| `DUCKLESS_METRICS_URI` | where the runner writes its metrics |
| `GOOGLE_CLOUD_PROJECT` | the project |

Settings you can pass with `--env`:

| Variable | Default | |
| --- | --- | --- |
| `DUCKLESS_MEMORY_FRACTION` | `0.8` | share of the VM memory given to DuckDB |
| `DUCKLESS_GCS_GRPC` | `true` (`false` on Cloud Run Jobs) | GCS over gRPC; `false` for HTTP |
| `DUCKLESS_THREADS` | the VM's vCPUs (the job's vCPUs on Cloud Run Jobs) | DuckDB threads |
| `DUCKLESS_SCRATCH_DIR` | `/mnt/disks/scratch` | where local SSD is mounted (spill goes under it) |
