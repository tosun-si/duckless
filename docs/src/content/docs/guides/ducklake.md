---
title: DuckLake tables
description: Tables with snapshots, time travel and concurrent writers, stored as Parquet on GCS with a Cloud SQL catalog.
---

[DuckLake](https://ducklake.select) turns Parquet files on GCS into tables: `INSERT`,
`UPDATE`, `DELETE`, snapshots and time travel, with several jobs writing at once. The data
stays Parquet in your bucket; a Postgres database keeps the catalog (which files make which
table version).

## Why a database

DuckDB alone, like Dataflow or Spark, is a compute engine: it reads and writes files and needs
nothing else. That is how DuckLess runs by default, with nothing left running between jobs.

DuckLake is a table format: it has to remember which files make which version of which table,
and that memory is the catalog. Every table format has one (Iceberg uses BigLake Metastore or a
REST catalog, Delta a Unity or Hive metastore). DuckLake keeps it in a plain SQL database, here
Cloud SQL for PostgreSQL. Turn DuckLake on when you need tables that change (updates, deletes,
corrections, several writers); for data that is only appended or fully recomputed, plain Parquet
is simpler and costs nothing between jobs.

## Turn it on

```bash
duckless init --project my-project --ducklake
```

`init` adds to the installation:

| Resource | Details |
| --- | --- |
| Cloud SQL for PostgreSQL 16 | `<name>-catalog`, private IP only, IAM authentication, daily backups and 7 days of point-in-time recovery |
| Database `ducklake` | the catalog |
| IAM database user | the runner service account; no password exists |
| Private services access | only if the network has none: a /20 range (`<name>-psa`) and the peering with Google services, set up by `init` itself |
| Runner roles | `roles/cloudsql.client`, `roles/cloudsql.instanceUser` |

It takes about 10 more minutes than a plain `init` (Cloud SQL), and costs the instance:
`db-g1-small` by default, about $25 a month. Add the two lines `init` prints to `.envrc`:

```bash
export DUCKLESS_DUCKLAKE_INSTANCE=my-project:europe-west1:duckless-catalog
export DUCKLESS_DUCKLAKE_DATA_PATH=gcss://my-project-duckless-work/lake/
```

## Use it

Every job then starts with the catalog attached as `lake`:

```sql
CREATE TABLE lake.orders AS SELECT * FROM read_parquet('gs://my-data/orders/*.parquet');

INSERT INTO lake.orders SELECT * FROM read_parquet('gs://my-data/orders_today/*.parquet');

SELECT count(*) FROM lake.orders AT (VERSION => 3);   -- time travel
SELECT * FROM ducklake_snapshots('lake');              -- history
```

In Python, `duckless_runtime.connect()` returns a connection with `lake` attached.

Jobs run on Cloud Batch or Cloud Run Jobs alike: Cloud Run jobs get Direct VPC egress to the
catalog's private IP (private ranges only; Google APIs keep their usual path).

## How it connects

The runner starts the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy)
next to DuckDB with automatic IAM authentication. The proxy logs in as the runner service
account and refreshes its token, so:

- no password is stored, passed or logged anywhere;
- jobs longer than an hour keep working (the IAM token lives one hour; DuckLake opens new
  catalog connections all along the job). This was checked with 80-minute jobs on both runners.

The data path is `gcss://`, not `gs://`: DuckLake hands `gs://` paths to the `httpfs`
extension, which the runner does not ship, while `gcss://` goes to the `gcs` extension and its
ADC credentials.

## Good to know

- **The catalog is the lake.** Without it, the Parquet files are files, not tables. Backups and
  point-in-time recovery are on; `duckless destroy` refuses to delete the instance without
  `--force`.
- **The runner is `cloudsqlsuperuser` on the catalog instance.** The instance holds only this
  catalog, and the runner already owns the lake's files on GCS. Grants on one database cannot be
  set from Terraform here (no public IP, so no SQL session from Infrastructure Manager).
- **Network.** The catalog lives on the jobs' network (`--network`, default `default`). If that
  network already has private services access (for other Cloud SQL instances, for example),
  `init` reuses it and never rewrites its ranges.
- **`destroy` keeps the private services access.** Other Cloud SQL instances of the network may
  use it, and Google refuses to remove a peering right after an instance is deleted. `destroy`
  prints the command to remove it once nothing uses it:
  `gcloud services vpc-peerings delete --network <network> --service servicenetworking.googleapis.com`.
- **Upgrades keep the catalog.** `duckless init` without `--ducklake` keeps what the
  deployment has. Removing it takes `--no-ducklake` and fails while deletion protection is on:
  only `destroy --force` turns that off.

## Several projects, one catalog

Data platforms often spread pipelines over several projects (one per domain or team). One
DuckLake catalog per project means one Cloud SQL instance per project; a single instance in a
shared project, with one database per lake, costs less and leaves one instance to back up,
monitor and upgrade.

:::caution[Not automated yet]
`duckless init --ducklake` creates a catalog in the installation's own project. Pointing an
installation at a catalog in another project is planned, not available yet. The constraints
below hold whatever the tooling.
:::

**Network.** The catalog has a private IP only, so jobs in the other projects must reach it:

| Option | Works | |
| --- | --- | --- |
| Shared VPC: the instance and the jobs on the host project's network | yes | the usual setup in large organizations; nothing more to configure |
| Private Service Connect: the instance allows a list of consumer projects | yes | when there is no Shared VPC; the Cloud SQL Auth Proxy supports it |
| VPC peering between the projects' networks | no | private services access is itself a peering, and peerings are not transitive |

**Isolation.** On an instance of its own, the runner being `cloudsqlsuperuser` gives it nothing
it does not already have. On a shared instance it would let one team read or break another
team's catalog: each lake needs its own database, and each runner account grants on its database
only.

**Data.** The catalog is central; the Parquet files stay in each project's own bucket, owned by
the team that writes them.

**Availability.** Every lake depends on the shared instance: give it high availability
(`REGIONAL`) and more than a `db-g1-small`. It still costs less than an instance per project
from four or five projects on.

