# DuckLess Terraform module

Everything DuckLess needs in a GCP project: a work bucket, a least-privilege runner service
account, access to the runner image, and optionally a DuckLake catalog and BigQuery read access.

Use it from your own Terraform, applied by your CI/CD with your deployment identity. That is the
recommended way for teams: `duckless init` applies this same module through Infrastructure
Manager, and is only a shortcut to start quickly. **Neither is required by the CLI**: DuckLess jobs
only need the settings this module outputs (`envrc`).

```hcl
module "duckless" {
  source = "git::https://github.com/tosun-si/duckless.git//duckless/terraform?ref=v0.4.1"

  project_id        = "my-project"
  region            = "europe-west1"
  runner_image_tag  = "0.4.1"
  data_buckets      = ["my-lake"]
  bigquery_datasets = ["sales"]
}

output "envrc" {
  value = module.duckless.envrc
}
```

A complete caller, with DuckLake on a Shared VPC: [`examples/terraform`](../../examples/terraform/main.tf).

## Requirements

To build these resources with your own modules instead of this one, follow the
[Requirements](https://tosun-si.github.io/duckless/reference/requirements/) page: every resource,
API and IAM role, split into DuckLess, DuckLake, BigQuery and job submitters.

| | |
| --- | --- |
| Terraform | `>= 1.5` |
| Provider `hashicorp/google` | `>= 7.18` (validated in CI with 7.18.0 and the latest release) |
| Applying identity | can enable services, create a service account, a bucket and an Artifact Registry repository, and grant project roles to the runner account. With `ducklake`: create a Cloud SQL instance. With `bigquery_datasets`: manage the IAM of those datasets. |

The module only sets a minimum provider version: your root module and its lock file decide the
exact one. A DuckLess release that needs a newer minimum says so in its release notes.

## Inputs

| Name | Default | |
| --- | --- | --- |
| `project_id` | | project where the jobs run |
| `region` | `europe-west1` | region of the work bucket, the jobs and the catalog |
| `name` | `duckless` | prefix of the created resources |
| `runner_image_tag` | `edge` | runner image tag: pin it to the CLI version your jobs use |
| `data_buckets` | `[]` | buckets the jobs read and write, in any project (the work bucket is always included) |
| `bigquery_datasets` | `[]` | BigQuery datasets the jobs read: `dataset` (this project) or `project.dataset` |
| `bigquery_jobs` | `false` | let jobs run BigQuery queries (`bigquery_query`, `ATTACH`) |
| `ducklake` | `false` | create a DuckLake catalog: Cloud SQL Postgres, private IP, IAM authentication, backups |
| `network`, `subnetwork` | `default` | network of the jobs and of the catalog: a name in this project, or a full path for a Shared VPC |
| `catalog_tier` | `db-g1-small` | Cloud SQL tier of the catalog |
| `work_bucket_name` | `<project>-<name>-work` | name of the work bucket |
| `runs_retention_days` | `30` | days after which job sources and metrics are deleted |
| `force_destroy` | `false` | let a destroy delete a non-empty work bucket and the catalog |
| `runner_image_registry`, `runner_image_path` | `https://ghcr.io`, `tosun-si/duckless-runner` | where the runner image comes from |
| `labels` | `{}` | labels on every resource (cost tracking) |
| `job_submitters` | `[]` | members who run jobs (`group:…`, a CI `serviceAccount:…`): Batch and Cloud Run job rights, actAs on the runner, work bucket, logs |
| `catalog_final_backup_days` | `30` | days a final backup of the catalog is kept after its deletion |

## Outputs

| Name | |
| --- | --- |
| `envrc` | the `export DUCKLESS_…` lines the CLI reads |
| `work_bucket`, `runner_service_account`, `runner_image` | the settings, one by one |
| `ducklake_instance`, `ducklake_data_path` | the catalog, when `ducklake` is on |
| `network`, `subnetwork`, `bigquery_datasets`, `bigquery_jobs` | echoed inputs (`duckless init` keeps them on upgrades) |

## DuckLake and the network

The catalog has a private IP only: the network needs **private services access** (a reserved range
and the peering with `servicenetworking.googleapis.com`). It is usually there already on a Shared
VPC; otherwise create it in your network code (`google_compute_global_address` +
`google_service_networking_connection`). The module does not manage it: the peering is shared by
every Cloud SQL instance of the network, and outlives this module.

## Upgrading

Change `ref` (and `runner_image_tag`) to the new version, plan, apply. The release notes list what
changes in the module.
