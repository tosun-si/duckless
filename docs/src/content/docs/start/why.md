---
title: Why DuckLess
description: The gap DuckLess fills between Cloud Run, BigQuery and running DuckDB yourself.
---

DuckDB is a single-node engine that gets the most out of a big machine: lots of memory,
lots of cores, a fast local disk to spill on. It doesn't need a cluster. What it needs is
a large VM for the few minutes a job takes, and that is surprisingly awkward to get on
Google Cloud.

## What you would use today

**Cloud Run jobs** are the usual home for "a Python job that crunches files". They are
capped at 32 GiB of memory and have no local disk, so a job that reads big files ends up
streaming everything by hand to stay under the limit, or failing with an OOM.

**BigQuery** is great at scanning and aggregating, but it gets expensive on
compute-heavy transformations, bills logical bytes (often 3 to 4 times the size of your
Parquet files), and leaves little room for code: business rules in Python, libraries, unit
tests.

**MotherDuck**, the managed DuckDB service, runs on AWS. Reading GCS from there means
HMAC keys, cross-cloud egress, and no say on the region.

**Doing it yourself** with `gcloud batch jobs submit` works, but every team ends up
rewriting the same things: GCS auth for DuckDB, spill configuration, networking, quotas,
job follow-up.

## What DuckLess does

DuckLess is that last option, done once:

- a CLI that submits your SQL file, Python script or own image to **Cloud Batch**, on the
  machine you pick, in your project;
- a **runner image** where DuckDB already reads and writes GCS through the VM's service
  account, spills to local SSD, and sizes its memory and threads to the machine;
- a `duckless init` command that sets up the few resources this needs in your project,
  with **Infrastructure Manager**, and removes them with `duckless destroy`.

Nothing runs between jobs. You pay for the VM while the job runs, and that's it.

## When it is a good fit

- Transformations on a few GB to a few hundred GB of Parquet on GCS.
- Python jobs that outgrew Cloud Run's memory limit.
- Heavy SQL (big joins, window functions, several passes) where BigQuery's on-demand
  pricing hurts.
- Teams that want their data, region and IAM to stay theirs.

## When it is not

- Interactive queries that must answer in a second: a job takes about a minute to get its
  VM. Positioning is cost, simplicity and open formats, not latency.
- Petabyte scans: that is BigQuery's job.

The [benchmarks](/duckless/benchmarks/) page shows the numbers behind these claims.
