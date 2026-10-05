# DuckLess

Serverless DuckDB on GCP. Submit SQL or your own code; DuckLess runs it on a right-sized
Compute Engine VM (Cloud Batch) **in your project**, reads and writes GCS through ADC
(no HMAC keys), spills on local SSD, then tears the VM down. Nothing runs between jobs.

> Status: v0.1.0 in progress. See `spike/README.md` for the measurements behind the design.

## Quick start

```bash
uv tool install .                            # or: uv run duckless …

duckless init --project my-project --region europe-west1
# prints the lines to put in .envrc (DUCKLESS_PROJECT, DUCKLESS_BUCKET, DUCKLESS_SA, DUCKLESS_IMAGE)

duckless preflight --machine n2-highmem-32 --spot
duckless run job.sql --machine n2-highmem-32 --spot
duckless exec --image <your-image> --machine n2-highmem-16 -- dbt build
duckless status <job-id>
duckless logs <job-id> --follow
duckless result <job-id>
duckless destroy --project my-project       # removes what init created
```

`init` applies the Terraform module shipped with the CLI (`duckless/terraform`) through
Infrastructure Manager: no local Terraform, state kept in your project, re-run it to upgrade.
Teams managing infra as code can use the same module directly instead.

## Writing jobs

- **SQL** (`.sql`): `${VAR}` placeholders come from the job env (`DUCKLESS_BUCKET`, `--env K=V`).
- **Python** (`.py`): `from duckless_runtime import connect` gives a DuckDB connection with GCS auth,
  spill on local SSD, memory and threads sized to the VM.
- **Write to GCS in parallel**: `COPY … TO 'gs://…' (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB')`
  is 7-8x faster than the single-writer default (~900 MB/s vs ~110 MB/s on 32 vCPU).
- **Vectorize Python logic**: a row-wise Python UDF runs at ~8k rows/s on one thread; use an
  Arrow UDF over numpy (`type="arrow"`) or SQL.

## Layout

Hexagonal, kept light: a pure core, ports, adapters, and one wiring point.

| Path | What |
| --- | --- |
| `duckless/core/` | Pure rules, no I/O: machine types and local SSD counts, job spec and planning, quotas, preflight |
| `duckless/ports.py` | What the service needs from outside: `Executor`, `ArtifactStore`, `LogReader`, `QuotaReader` (Protocols) |
| `duckless/service.py` | Operations shared by the CLI, the SDK and later the SaaS control plane: functions taking ports as arguments |
| `duckless/adapters/` | GCP implementations: Cloud Batch, GCS, Cloud Logging, Compute quotas |
| `duckless/wiring.py` | Binds the service functions to the adapters (lazily) |
| `duckless/cli.py` | `duckless` command, a driving adapter |
| `runtime/` | Runner image (`duckless_runtime`), published as `ghcr.io/tosun-si/duckless-runner`: DuckDB + `gcs` community extension, tuned for the VM |
| `duckless/terraform/` | APIs, work bucket, least-privilege runner service account, Artifact Registry remote repository proxying the runner image |
| `spike/` | The spike that validated the approach, kept as a record |

Dependency rule: `core` imports nothing else from DuckLess, `service` only `core` and `ports`,
and only `wiring` imports `adapters`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

License: Apache-2.0
