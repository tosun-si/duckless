---
title: Quick start
description: Install the CLI, set up a project and run a first job in about ten minutes.
---

## Before you start

- A Google Cloud project with billing enabled, and permission to enable APIs, create
  service accounts and grant IAM roles on it (`roles/owner` is the simple case).
- [Application Default Credentials](https://cloud.google.com/docs/authentication/provide-credentials-adc):
  `gcloud auth application-default login`.
- Python 3.13 and [uv](https://docs.astral.sh/uv/) (or pip).
- A subnetwork with **Private Google Access** in the region you use: job VMs have no
  external IP. The `default` network has it on in most regions; check with
  `gcloud compute networks subnets describe default --region <region> --format="value(privateIpGoogleAccess)"`.

## 1. Install

```bash
uv tool install duckless      # or: pip install duckless
duckless --help
```

## 2. Set up the project

```bash
duckless init --project my-project --region europe-west1
```

:::note[init is a shortcut, not a requirement]
`init` applies the DuckLess Terraform module for you. Teams that keep their infrastructure as code
call the same module from their own Terraform and CI/CD instead, and skip `init` entirely: see
[Infrastructure](/duckless/guides/infrastructure/#your-terraform).
:::

`init` enables the APIs it needs, then lets Infrastructure Manager create a work bucket, a
service account for the job VMs and a proxy for the runner image (details in
[Infrastructure](/duckless/guides/infrastructure/)). It takes a few minutes and ends with
lines like these:

```bash
export DUCKLESS_PROJECT=my-project
export DUCKLESS_REGION=europe-west1
export DUCKLESS_BUCKET=my-project-duckless-work
export DUCKLESS_SA=duckless-runner@my-project.iam.gserviceaccount.com
export DUCKLESS_IMAGE=europe-west1-docker.pkg.dev/my-project/duckless-runner/tosun-si/duckless-runner:0.4.1
```

Put them in your `.envrc` (or export them in your shell). Every other command reads them.

## 3. Check a machine

```bash
duckless preflight --machine n2-highmem-16 --spot
```

`preflight` checks that the machine accepts the local SSD count DuckLess will attach, and
that the region has enough quota for it.

## 4. Run a first job

Save this as `hello.sql`. It generates a small TPC-H dataset, writes it to your work
bucket as Parquet, and reads it back:

```sql
CALL dbgen(sf = 1);
COPY lineitem TO 'gs://${DUCKLESS_BUCKET}/hello/lineitem'
  (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB');
SELECT l_returnflag, count(*) AS lines, round(sum(l_extendedprice), 2) AS revenue
FROM read_parquet('gs://${DUCKLESS_BUCKET}/hello/lineitem/*.parquet')
GROUP BY ALL ORDER BY ALL;
```

```bash
duckless run hello.sql --machine n2-highmem-16 --spot
```

The CLI follows the job until it ends: about a minute for the VM to start, a few seconds to
run. Then:

```bash
duckless result <job-id>    # rows of the last SELECT
duckless logs <job-id>      # runner logs from Cloud Logging
```

## Clean up

```bash
duckless destroy --project my-project --force
```

`--force` also deletes the work bucket if it still holds files.

## Next

- Run the [examples](/duckless/examples/): daily marts, late corrections with DuckLake, raw data
  validation, BigQuery to Python. The first one generates the data the others use.
- Read [Writing jobs](/duckless/guides/writing-jobs/) for the habits that keep jobs fast, and
  [Machines, Spot and spill](/duckless/guides/machines/) to size them.
