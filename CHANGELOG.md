# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The CLI, the Python package
and the runner image share one version.

## [Unreleased]

## [0.1.0] - 2026-10-05

First release: serverless DuckDB on Google Cloud. Jobs run on a right-sized Compute
Engine VM (Cloud Batch) in your own project, read and write GCS through ADC (no HMAC
keys), spill on local SSD, and the VM is deleted when the job ends.

### Added

- `duckless init` / `duckless destroy`: deploy, upgrade and remove the DuckLess
  infrastructure of a project with Infrastructure Manager (work bucket, least-privilege
  runner service account, Artifact Registry remote repository for the runner image).
  Prints the `.envrc` lines to use.
- `duckless run job.sql|job.py`: run SQL or Python on the default runner;
  `${VAR}` placeholders in SQL come from the job env.
- `duckless exec --image … -- <command>`: run your own image and command (dbt, scripts).
- `duckless status`, `logs [--follow]`, `result`, `cancel`.
- `duckless preflight`: checks a machine type, its local SSD count and the region's
  quotas before submitting (Spot falls back on standard quotas when the project has no
  preemptible quota).
- Machine rules: local SSD counts allowed per N2 size, checked client-side.
- Runner image `ghcr.io/tosun-si/duckless-runner`: DuckDB with the `gcs` community
  extension (ADC), gRPC transport, memory and threads sized to the VM (cgroup CPU
  quota aware), spill on local SSD, JSON logs and per-job metrics.
- `duckless_runtime.connect()` for Python jobs and `copy_to_parquet()` for parallel
  Parquet writes to GCS (~900 MB/s vs ~110 MB/s with a single writer on 32 vCPU).
- Terraform module `duckless/terraform`, usable directly by teams managing infra as code.

[Unreleased]: https://github.com/tosun-si/duckless/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tosun-si/duckless/releases/tag/v0.1.0
