---
name: setup
description: Install DuckLess and deploy it in a GCP project with `duckless init`, then wire the .envrc. Use when someone starts with DuckLess, adds it to a new project, upgrades it, or removes it with `duckless destroy`.
---

# Setting up DuckLess

DuckLess runs DuckDB jobs on right-sized VMs (Cloud Batch) or containers (Cloud Run Jobs) in
the user's own GCP project. Everything it needs is created by `duckless init`, through
Infrastructure Manager, with the caller's credentials.

## Steps

1. Install the CLI: `uv tool install duckless` (or `pip install duckless`). Python 3.13.
2. Authenticate: `gcloud auth login --update-adc`. The caller needs to be able to enable APIs,
   create service accounts and grant project roles (Owner, or an admin set equivalent).
3. Check first, nothing is created: `duckless preflight -m n2-highmem-16 --project <project>`
   tells whether the region has the quota.
4. Deploy: `duckless init --project <project>` (region: `--region`, default `europe-west1`).
   - `--data-bucket <bucket>` (repeatable): existing buckets jobs may read and write.
   - `--ducklake`: also create a DuckLake catalog (see the `ducklake` skill). About 10 more minutes.
   - `--network <vpc>`: network of the jobs; `default` otherwise.
5. Paste the `export …` lines `init` prints into the project's `.envrc` (direnv), then
   `direnv allow`. Never commit them to a `.env` file; they are not secrets but are per-project.
6. First job: `duckless run job.sql -m n2-standard-4` (see the `writing-jobs` skill).

## What `init` checks and creates

- Organization policies that would make the apply fail (service account creation, allowed
  locations, CMEK, restricted services). On `BLOCKED`, nothing was created: read the failing
  check, fix the policy or pick another region, run `init` again.
- A work bucket, a runner service account (least privilege), an Artifact Registry remote
  repository proxying the public runner image (job VMs have no external IP), and, with
  `--ducklake`, a Cloud SQL catalog.

## Upgrades and removal

- `duckless init` again with a newer CLI upgrades in place. Options left out keep the
  deployment's values (`--ducklake` included); `--no-ducklake` is explicit.
- `duckless destroy --project <project>` removes what `init` created. `--force` is needed when
  the work bucket holds objects or a DuckLake catalog exists: confirm with the user first, it
  deletes data. The network peering of DuckLake is kept (shared); `destroy` prints how to remove it.

Docs: https://tosun-si.github.io/duckless/start/quickstart/
