---
name: setup
description: Set DuckLess up in a GCP project, either with its Terraform module in the team's own Terraform and CI/CD (recommended for teams) or with `duckless init` (quick start), then wire the .envrc. Use when someone starts with DuckLess, adds it to a project, integrates the module into Terraform or Terragrunt, upgrades it, or removes it.
---

# Setting up DuckLess

DuckLess runs DuckDB jobs on right-sized VMs (Cloud Batch) or containers (Cloud Run Jobs) in
the user's own GCP project. Everything it needs is created by `duckless init`, through
Infrastructure Manager, with the caller's credentials.

## Pick the way

`duckless init` is **not required**: the CLI only reads the settings (`DUCKLESS_*`) the DuckLess
Terraform module outputs. Ask how the team manages infrastructure:

- **They keep infrastructure as code** (Terraform, Terragrunt, a CI/CD pipeline): use the module.
  Their deployment identity applies it; no `duckless-infra` account is created. This is the
  recommended way for teams.
- **A quick try, a sandbox project**: `duckless init`, five minutes.

## With the team's Terraform

1. Add the module where their other stacks live, pinned to the CLI version they install:

   ```hcl
   module "duckless" {
     source           = "git::https://github.com/tosun-si/duckless.git//duckless/terraform?ref=v<version>"
     project_id       = "<project>"
     region           = "europe-west1"
     runner_image_tag = "<version>"
     data_buckets      = [...]          # buckets jobs read and write, any project
     bigquery_datasets = [...]          # `dataset` or `project.dataset`
     # ducklake = true, network/subnetwork = full paths on a Shared VPC
   }
   output "envrc" { value = module.duckless.envrc }
   ```

   Inputs and requirements: `duckless/terraform/README.md`; a full caller: `examples/terraform/main.tf`.
2. Terragrunt: a unit whose `terraform { source = "git::…//duckless/terraform?ref=v<version>" }`,
   with the inputs in `inputs = { … }`; follow the repository's existing unit layout.
3. Provider: the module needs `hashicorp/google >= 7.18`; their lock file decides the exact version.
4. DuckLake: the network needs private services access (often already there on a Shared VPC); if
   not, it belongs in their network code, not in the module.
5. Plan in CI, apply after review, then put the `envrc` output in the project's `.envrc`.
6. Upgrades: bump `ref` and `runner_image_tag` together, read the release notes, plan, apply.

Help them **wire** the module; do not copy its resources into their code: a copy drifts and misses
the fixes of later releases.

**When they must use their own modules** (company rules on naming, CMEK, labels…): build the
resources from the Requirements page (https://tosun-si.github.io/duckless/reference/requirements/),
layer by layer: DuckLess (APIs, work bucket with the `runs/` lifecycle, runner account and its roles,
image reachable without external IP, Private Google Access), then DuckLake only if they use it
(private services access, Cloud SQL with IAM auth, the runner's IAM database user and Cloud SQL
roles), BigQuery only if they read it, and the job submitters' roles. Map each requirement to their
module's inputs, keep the `DUCKLESS_*` settings as outputs, and tell them to check the release
notes for requirement changes on each upgrade.

## With duckless init

1. Install the CLI: `uv tool install duckless` (or `pip install duckless`). Python 3.13.
2. Authenticate: `gcloud auth login --update-adc`. The caller needs to be able to enable APIs,
   create service accounts and grant project roles (Owner, or an admin set equivalent).
3. Check first, nothing is created: `duckless preflight -m n2-highmem-16 --project <project>`
   tells whether the region has the quota.
4. Deploy: `duckless init --project <project>` (region: `--region`, default `europe-west1`).
   - `--data-bucket <bucket>` (repeatable): existing buckets jobs may read and write.
   - `--ducklake`: also create a DuckLake catalog (see the `ducklake` skill). About 10 more minutes.
   - `--bigquery-dataset <dataset>` (repeatable): datasets of the project jobs may read.
   - `--network <vpc>`: network of the jobs; `default` otherwise.
5. Paste the `export …` lines `init` prints into the project's `.envrc` (direnv), then
   `direnv allow`. They are not secrets but are per-project.
6. First job: `duckless run job.sql -m n2-standard-4` (see the `writing-jobs` skill).

The `duckless-infra` account `init` creates holds its roles only while `init` or `destroy` runs,
and its project IAM admin role is limited by a condition to the runner's roles.

## What `init` checks and creates

- Organization policies that would make the apply fail (service account creation, allowed
  locations, CMEK, restricted services). On `BLOCKED`, nothing was created: read the failing
  check, fix the policy or pick another region, run `init` again.
- A work bucket, a runner service account (least privilege), an Artifact Registry remote
  repository proxying the public runner image (job VMs have no external IP), and, with
  `--ducklake`, a Cloud SQL catalog.

## Upgrades and removal (init)

- `duckless init` again with a newer CLI upgrades in place. Options left out keep the
  deployment's values (`--ducklake` included); `--no-ducklake` is explicit.
- `duckless destroy --project <project>` removes what `init` created. `--force` is needed when
  the work bucket holds objects or a DuckLake catalog exists: confirm with the user first, it
  deletes data. The network peering of DuckLake is kept (shared); `destroy` prints how to remove it.

Docs: https://tosun-si.github.io/duckless/start/quickstart/
