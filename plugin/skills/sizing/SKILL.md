---
name: sizing
description: Choose the machine, Spot, local SSD and runner (Cloud Batch or Cloud Run Jobs) for a DuckLess job, and resize it from the metrics of a previous run. Use when deciding `-m`, `--spot`, `--local-ssd` or `--on` for `duckless run`/`exec`, or when a job is slow, spills, runs out of memory or costs too much.
---

# Sizing a DuckLess job

The machine (`-m`) is the main decision; the runner follows from it.

## 1. Start from the data

- Estimate the working set: the Parquet the job reads after filters and column pruning, then
  roughly 2-5x that for joins and aggregations in memory. Sizes: `gcloud storage du -s gs://…`.
- DuckDB gets about 80% of the machine's memory (60% on Cloud Run). Beyond that it spills.
- Pick the family by ratio: `n2-highmem-*` (8 GB/vCPU) for joins and aggregations, the
  default choice; `n2-standard-*` (4 GB/vCPU) for scans and simple transforms;
  `*-highcpu-*` rarely helps.
- Reference point: TPC-H SF100 (~100 GB) ran its 22 queries in 47 s on `n2-highmem-32` Spot.

## 2. Runner: `--on auto` decides

`auto` (default) sends a job to **Cloud Run Jobs** only when nothing changes for it: the
machine fits (at most 8 vCPU and 32 GiB) and neither `--spot` nor `--local-ssd` is asked.
Everything else goes to **Cloud Batch**.

| | Cloud Run Jobs | Cloud Batch |
| --- | --- | --- |
| Start | ~30 s | ~1 min |
| Spill | memory only | local SSD |
| Spot | no | yes |

Force with `--on batch` when a small job may spill heavily (Cloud Run has no disk).

## 3. Spill and Spot

- Spill goes to local SSD on Batch (`--local-ssd N`, 375 GB each; the smallest valid count is
  the default). More SSDs for heavy sorts or joins on large data.
- `--spot` cuts the VM price by ~60-90% and retries on preemption: right for jobs that can be
  re-run (idempotent writes with `OVERWRITE`), wrong for a job that must finish by a deadline.

## 4. Check quota before, read metrics after

- `duckless preflight -m <machine> [--spot] [--local-ssd N]` checks the regional quota.
- `duckless status <job-id>` shows `peak rss` and `spill`. Peak RSS well under the memory
  limit and no spill: go smaller (or to Cloud Run). Spill or OOM: more memory first
  (highmem, bigger size), then more local SSD.

Docs: https://tosun-si.github.io/duckless/guides/machines/
