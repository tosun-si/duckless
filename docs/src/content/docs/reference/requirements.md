---
title: Requirements
description: Every resource, API and IAM role DuckLess needs, with why, for teams that build them with their own Terraform modules.
---

The [Terraform module](https://github.com/tosun-si/duckless/tree/main/duckless/terraform) and
`duckless init` create everything below. When a company's rules call for its own modules (naming,
CMEK, labels, a bucket module of its own…), build the same resources with them: this page is the
specification. The CLI does not care how they were created; it only reads the settings at the end.

Each release says in its notes whether these requirements change.

The requirements come in layers: **DuckLess** (DuckDB jobs on files) is always needed; **DuckLake**
and **BigQuery** only when you use them; **job submitters** are the people or CI identities that
run jobs.

## 1. DuckLess: DuckDB jobs on GCS

Everything a job needs to run SQL or Python on files in GCS.

### APIs

| API | Why |
| --- | --- |
| `batch.googleapis.com` | jobs on Compute Engine VMs |
| `run.googleapis.com` | small jobs on Cloud Run Jobs |
| `compute.googleapis.com` | the VMs, quotas (`preflight`) |
| `storage.googleapis.com` | work bucket, data |
| `logging.googleapis.com`, `monitoring.googleapis.com` | job logs and metrics |
| `artifactregistry.googleapis.com` | the runner image |

### Work bucket

| | |
| --- | --- |
| Location | the jobs' region |
| Access | uniform bucket-level access; public access prevention enforced |
| Lifecycle | delete objects under `runs/` after N days (default 30): job sources, metrics, results |
| Content | `runs/<job-id>/` written by the CLI and the runner; nothing else is required |

One work bucket per installation: sharing it between teams would let each read the others' job
sources and results. Data stays in your own buckets (below).

### Runner service account

The identity the jobs run as (Batch VMs and Cloud Run jobs).

| Role | On | Why |
| --- | --- | --- |
| `roles/batch.agentReporter` | project | the Batch agent on the VM reports task status |
| `roles/logging.logWriter` | project | job logs |
| `roles/monitoring.metricWriter` | project | VM metrics |
| `roles/storage.objectUser` | work bucket | read job sources, write metrics and results |
| `roles/storage.objectUser` | each data bucket | read and write your data (read-only data: `roles/storage.objectViewer`) |
| `roles/artifactregistry.reader` | the image repository | pull the runner image (and your own images for `exec`) |

### Runner image

`ghcr.io/tosun-si/duckless-runner:<version>`, the same version as the CLI. Job VMs have no external
IP, so the image must come from a registry reachable through Private Google Access: an Artifact
Registry **remote repository** proxying `https://ghcr.io` (what the module creates), or a copy in
your internal registry. Images for `duckless exec` should be built `FROM` it.

### Network

| | |
| --- | --- |
| Batch VMs | no external IP: the subnetwork needs **Private Google Access** (GCS, Artifact Registry, Logging) |
| Cloud Run jobs | no VPC needed (except with DuckLake, below) |
| Shared VPC | give the full network and subnetwork paths (`projects/<host>/…`) |

### Settings the CLI reads

| Variable | Value |
| --- | --- |
| `DUCKLESS_PROJECT` | project where jobs run |
| `DUCKLESS_REGION` | region of the jobs and the work bucket |
| `DUCKLESS_BUCKET` | work bucket name |
| `DUCKLESS_SA` | runner service account email |
| `DUCKLESS_IMAGE` | full runner image reference |
| `DUCKLESS_NETWORK`, `DUCKLESS_SUBNETWORK` | network and subnetwork (default `default`) |

## 2. DuckLake: tables with a catalog

Only with DuckLake. Adds a Postgres catalog that every job attaches as `lake`.

### APIs

`sqladmin.googleapis.com`, `servicenetworking.googleapis.com`.

### Network: private services access

The catalog has a private IP only. The jobs' network needs **private services access**: a reserved
internal range (`purpose = VPC_PEERING`, a `/20` is plenty) and the peering with
`servicenetworking.googleapis.com`. It is shared by every Cloud SQL instance of the network and
usually already exists on a Shared VPC; it belongs in network code, not with DuckLess.

Cloud Run jobs reach it with **Direct VPC egress** (private ranges only): DuckLess configures it on
the jobs when the catalog is set; the subnetwork must allow it.

### Cloud SQL instance

| Setting | Value | Why |
| --- | --- | --- |
| Engine | PostgreSQL 16 | |
| IP | private only (`ipv4_enabled = false`, the jobs' network) | no public endpoint |
| Flag | `cloudsql.iam_authentication = on` | jobs log in with their service account, no password |
| SSL mode | `ENCRYPTED_ONLY` | |
| Backups | daily, 7 days of point-in-time recovery | the catalog is the lake |
| Final backup | on deletion, kept 30 days | deleting an instance deletes its automated backups |
| Deletion protection | on | |
| Tier | `db-g1-small` is enough (metadata only) | |
| Database | `ducklake` | |
| User | the runner account as a `CLOUD_IAM_SERVICE_ACCOUNT` user (its email without `.gserviceaccount.com`), with `cloudsqlsuperuser` | owns the catalog tables; on an instance shared with other teams, grant on its database only instead |

### Runner service account, in addition

| Role | On | Why |
| --- | --- | --- |
| `roles/cloudsql.client` | project | the Cloud SQL Auth Proxy in the runner connects |
| `roles/cloudsql.instanceUser` | project | IAM database login |

### Settings, in addition

| Variable | Value |
| --- | --- |
| `DUCKLESS_DUCKLAKE_INSTANCE` | instance connection name, `project:region:instance` |
| `DUCKLESS_DUCKLAKE_DATA_PATH` | `gcss://<work or data bucket>/lake/` (**`gcss://`, not `gs://`**) |

## 3. BigQuery: reading tables

Only to read BigQuery tables (`bigquery_scan`, `read_bigquery`).

| | On | Why |
| --- | --- | --- |
| APIs `bigquery.googleapis.com`, `bigquerystorage.googleapis.com` | project | |
| `roles/bigquery.readSessionUser` | the jobs' project | Storage Read API sessions (billed there) |
| `roles/bigquery.dataViewer` | each dataset read (any project) | read the tables |
| `roles/bigquery.jobUser` | the jobs' project, optional | only for queries: `bigquery_query`, `ATTACH … (TYPE bigquery)` |

## 4. Job submitters

The people or CI identities that run `duckless run` / `exec` / `status` / `logs`. The module grants
these to `job_submitters`.

| Role | On | Why |
| --- | --- | --- |
| `roles/batch.jobsEditor` | project | create, follow and cancel Batch jobs |
| `roles/run.developer` | project | create, run and cancel Cloud Run jobs |
| `roles/iam.serviceAccountUser` | the runner service account | jobs run as it (`actAs`) |
| `roles/storage.objectUser` | work bucket | upload job sources, read metrics and results |
| `roles/logging.viewer` | project | `duckless logs` |
| `roles/compute.viewer` | project, optional | `duckless preflight` reads regional quotas |

## Checklist

- [ ] APIs of section 1 (and 2, 3 as needed) enabled
- [ ] Work bucket in the jobs' region, with the `runs/` lifecycle rule
- [ ] Runner service account with the roles of section 1 (and 2, 3)
- [ ] Runner image reachable from VMs without external IP, at the CLI's version
- [ ] Private Google Access on the jobs' subnetwork
- [ ] DuckLake: private services access on the network, the Cloud SQL instance, its IAM user
- [ ] Job submitters granted section 4
- [ ] The `DUCKLESS_*` settings in the project's `.envrc`
