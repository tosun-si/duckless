---
title: Infrastructure
description: What duckless init creates in your project, the permissions involved, and how to remove it.
---

`duckless init` sets up a project once; jobs reuse it. It is safe to run again: that is how
you upgrade after installing a newer CLI.

## What init does

1. Enables the APIs Infrastructure Manager needs (`config`, `iam`, `cloudresourcemanager`,
   `serviceusage`, `storage`).
2. Checks the organization policies that would make the deployment fail (see below), and
   stops there, with nothing created, if one does.
3. Creates a service account for Infrastructure Manager, `duckless-infra`, and grants it the
   roles it needs to apply the module.
4. Uploads the Terraform module shipped with the CLI to a small staging bucket.
5. Asks Infrastructure Manager to apply it, and prints the `.envrc` lines from its outputs.

The module creates:

| Resource | Name | Purpose |
| --- | --- | --- |
| Work bucket | `<project>-duckless-work` | job sources and metrics under `runs/` (deleted after 30 days), your outputs anywhere else |
| Service account | `duckless-runner` | identity of the job VMs |
| Artifact Registry remote repository | `duckless-runner` | pulls the runner image from `ghcr.io` and caches it in your region |
| APIs | Batch, Cloud Run, Compute, Logging, Monitoring, Storage, Artifact Registry | |

## Permissions

The job VMs' account, `duckless-runner`, gets only:

- `roles/batch.agentReporter`, `roles/logging.logWriter`, `roles/monitoring.metricWriter`
  on the project;
- `roles/storage.objectUser` on the work bucket and on each `--data-bucket`;
- `roles/artifactregistry.reader` on the image repository.

The Infrastructure Manager account, `duckless-infra`, has admin roles on Artifact Registry,
Storage, service accounts, project IAM and service usage: it is what lets Terraform create
the resources above. It is removed by `duckless destroy`.

To let jobs read or write other buckets, list them at init time:

```bash
duckless init --project my-project --data-bucket my-lake --data-bucket my-exports
```

## Organization policies

In an organization, policies set on folders or on the organization can forbid what `init`
needs. Rather than letting Infrastructure Manager fail on them minutes later, `init` reads
the project's effective policies first:

| Constraint | Blocks `init` when |
| --- | --- |
| `iam.disableServiceAccountCreation` | it is enforced: `init` creates two service accounts |
| `gcp.resourceLocations` | the region is denied |
| `gcp.restrictServiceUsage` | one of the APIs DuckLess uses is not allowed |
| `gcp.restrictNonCmekServices` | Cloud Storage or Artifact Registry require customer-managed keys, which DuckLess does not set yet |

When `gcp.resourceLocations` allows a list of groups that doesn't name the region directly,
`init` only warns: a nested group may still cover it. Ask your organization administrators
for an exception on the project, or use a project where these constraints are relaxed.

## Networking

Job VMs have no external IP. They reach Google APIs (GCS, Artifact Registry, Logging)
through **Private Google Access**, which must be on for the job subnetwork. By default
DuckLess uses the `default` network and subnetwork of the region; set `DUCKLESS_NETWORK` and
`DUCKLESS_SUBNETWORK` to use others, including a Shared VPC subnet (full
`projects/…/regions/…/subnetworks/…` path).

## Upgrading

```bash
uv tool upgrade duckless
duckless init --project my-project
```

The second run updates the deployment with the module and runner image of the new version.

## Removing everything

```bash
duckless destroy --project my-project --force
```

`destroy` deletes the Cloud Run jobs of past runs (those running as this installation's
runner account), the deployment (bucket, service account, repository), then the
Infrastructure Manager account, its grants and the staging bucket. `--force` is needed when
the work bucket still holds objects. Enabled APIs stay enabled.

## Using the Terraform module directly

Teams that manage their infrastructure as code can use the module instead of `init`:

```hcl
module "duckless" {
  source = "git::https://github.com/tosun-si/duckless.git//duckless/terraform?ref=v0.3.1"

  project_id   = "my-project"
  region       = "europe-west1"
  data_buckets = ["my-lake"]
}

output "envrc" {
  value = module.duckless.envrc
}
```
