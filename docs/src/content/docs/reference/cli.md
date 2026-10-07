---
title: CLI
description: Every duckless command and option.
---

Every command accepts `--project` and `--region`, before or after its name. They default to
`DUCKLESS_PROJECT` and `DUCKLESS_REGION` (then `europe-west1`).

## Project setup

### `duckless init`

Deploys or upgrades DuckLess in a project with Infrastructure Manager and prints the
`.envrc` lines.

| Option | Default | |
| --- | --- | --- |
| `--data-bucket` | none | bucket jobs may read and write, repeatable |
| `--runner-tag` | the CLI version | runner image tag (`edge` for dev builds) |
| `--name` | `duckless` | prefix of the created resources |
| `--ducklake` / `--no-ducklake` | the deployment's (off for a new one) | DuckLake catalog on Cloud SQL, see [DuckLake tables](/duckless/guides/ducklake/) |
| `--network` | the deployment's (`default` for a new one) | VPC network of the jobs and of the catalog's private IP |

### `duckless destroy`

Deletes what `init` created.

| Option | Default | |
| --- | --- | --- |
| `--name` | `duckless` | prefix given to `init` |
| `--force` | off | also delete a work bucket that still holds objects, and the DuckLake catalog |
| `--yes` | off | do not ask for confirmation |

## Jobs

### `duckless run <file>`

Runs a `.sql` or `.py` file on the runner image.

### `duckless exec --image <image> -- <command…>`

Runs a command in an image; the command replaces the image entrypoint.

Both take:

| Option | Default | |
| --- | --- | --- |
| `--machine`, `-m` | `n2-highmem-16` | Compute Engine machine type |
| `--spot` | off | run on a Spot VM |
| `--local-ssd` | smallest count the machine accepts | number of 375 GB local SSDs for spill |
| `--env`, `-e` | none | `KEY=VALUE` passed to the job, repeatable |
| `--max-run-seconds` | `10800` | hard limit on the job duration |
| `--wait` / `--no-wait` | wait | follow the job until it ends |
| `--on` | `auto` | `auto`, `batch` or `cloudrun`: see [Cloud Batch or Cloud Run Jobs](/duckless/guides/machines/#cloud-batch-or-cloud-run-jobs) |

### `duckless status <job-id>`

State, timeline, and once the job is over, the runner's metrics: duration of each
statement, peak memory, peak spill.

### `duckless logs <job-id>`

Runner logs from Cloud Logging. `--follow` (`-f`) keeps printing until the job ends.

### `duckless result <job-id>`

Rows of the job's last `SELECT` (up to 20).

### `duckless cancel <job-id>`

Cancels a queued or running job.

## Checks

### `duckless preflight`

Checks a machine choice against Compute Engine rules and the region's quotas. Takes
`--machine`, `--spot` and `--local-ssd`, like `run`. Exits with 1 when something blocks.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | the job (or the deployment) failed, or `preflight` is blocked |
| 2 | invalid input or missing settings |
