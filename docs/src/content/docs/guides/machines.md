---
title: Machines, Spot and spill
description: Picking a machine, local SSD for spill, Spot VMs and quotas.
---

## Cloud Batch or Cloud Run Jobs

Jobs run on one of two platforms, chosen per job with `--on`:

| | Cloud Batch | Cloud Run Jobs |
| --- | --- | --- |
| Machine | any Compute Engine machine | up to 8 vCPU and 32 GiB |
| Local SSD for spill | yes | no (spill stays in memory) |
| Spot | yes | no |
| Measured start (submit to first statement) | about 1 minute | about 30 seconds |

`--on auto` (the default) picks Cloud Run Jobs only when nothing changes for the job: the
machine fits Cloud Run, and neither `--spot` nor `--local-ssd` is asked. Otherwise the job
runs on Cloud Batch. `--on batch` and `--on cloudrun` force one or the other.

```bash
duckless run small.sql -m n2-standard-4             # fits: Cloud Run Jobs, 4 vCPU / 16 GiB
duckless run big.sql -m n2-highmem-32 --spot        # Cloud Batch
duckless run small.sql -m n2-standard-4 --on batch  # Cloud Batch anyway
```

On Cloud Run, DuckLess sizes the job like the machine you asked for (rounded to the CPU steps
Cloud Run accepts: 32 GiB needs 8 vCPU), sets DuckDB's threads to those vCPUs, and reads GCS
over HTTP: over gRPC, the first call waits about 37 seconds there before falling back.

## Picking a machine

Any Compute Engine machine type works with `--machine`. For DuckDB, memory matters most, so
the `highmem` shapes are a good default:

| Machine | vCPU | Memory | Good for |
| --- | --- | --- | --- |
| `n2-highmem-8` | 8 | 64 GB | small jobs, tests |
| `n2-highmem-16` | 16 | 128 GB | the default |
| `n2-highmem-32` | 32 | 256 GB | tens to hundreds of GB of Parquet |
| `n2-highmem-64` | 64 | 512 GB | big joins over hundreds of GB |

DuckDB uses 80 % of the VM's memory and all its cores by default. To leave more room for
your own Python objects, lower the fraction:

```bash
duckless run job.py -m n2-highmem-32 -e DUCKLESS_MEMORY_FRACTION=0.6
```

## Spill on local SSD

When a query needs more memory than it has, DuckDB writes intermediate data to disk instead
of failing. DuckLess attaches local SSDs (375 GB each) to the VM and points DuckDB's
`temp_directory` at them.

In the benchmarks, TPC-H SF100 queries that use 35 GB of memory on a large VM still finish
with a 7 GB memory limit: about three times slower, without errors.

Compute Engine only accepts some SSD counts for each machine size, and Batch reports a
wrong count only after the job is queued. DuckLess checks it before submitting and picks the
smallest allowed count by default:

| N2 vCPUs | Allowed local SSD counts |
| --- | --- |
| 2 to 10 | 1, 2, 4, 8, 16, 24 |
| 12 to 20 | 2, 4, 8, 16, 24 |
| 22 to 40 | 4, 8, 16, 24 |
| 42 to 80 | 8, 16, 24 |
| 82 to 128 | 16, 24 |

`--local-ssd 0` runs without local SSD (spill then goes to the boot disk). For other
families, Compute Engine decides; C3 and C3D `-lssd` shapes come with their SSD built in.

## Spot VMs

`--spot` runs the job on a Spot VM: 60 to 90 % cheaper, but Google can take it back. A
preempted job is retried from the start (twice at most): DuckDB has no checkpoint. Spot is a
good fit for jobs of a few minutes to an hour; for long jobs that must finish, drop it.

Jobs may land in any zone of the region, which avoids most capacity errors on large Spot
shapes.

## Quotas

```bash
duckless preflight --machine n2-highmem-32 --spot
```

`preflight` lists the regional quotas the job needs and what is left. On a new project, the
Spot quota (`PREEMPTIBLE_CPUS`) is often 0: that is fine, Spot VMs then use the standard
CPU quota. Raise quotas from the console (IAM & Admin > Quotas) when `preflight` reports a
shortage.
