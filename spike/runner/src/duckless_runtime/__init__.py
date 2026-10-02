"""DuckLess runtime: a DuckDB connection tuned for the VM it runs on.

User code only needs `duckless_runtime.connect()`: GCS auth (ADC via the
`gcs` community extension), spill on local SSD, memory and threads are set.
"""

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

SCRATCH_DIR = Path(os.environ.get("DUCKLESS_SCRATCH_DIR", "/mnt/disks/scratch"))
MEMORY_FRACTION = float(os.environ.get("DUCKLESS_MEMORY_FRACTION", "0.8"))
# gRPC measured faster than HTTP on every read/write case of the spike.
GCS_GRPC = os.environ.get("DUCKLESS_GCS_GRPC", "true").lower() == "true"
CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")


@dataclass(frozen=True)
class RuntimeSettings:
    memory_limit_gb: int
    threads: int
    temp_directory: str
    temp_on_local_ssd: bool
    project_id: str | None
    gcs_grpc: bool


def log(event: str, severity: str = "INFO", **fields) -> None:
    """One JSON line per event on stdout, parsed by Cloud Logging."""
    print(json.dumps({"severity": severity, "message": event, "time": time.time(), **fields}, default=str), flush=True)


def _total_memory_bytes() -> int:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def cpus_from_quota(cpu_max: str) -> int | None:
    """cgroup v2 `cpu.max` is "<quota> <period>" or "max <period>"."""
    quota, _, period = cpu_max.strip().partition(" ")
    return max(1, int(quota) // int(period)) if quota.isdigit() and period.isdigit() else None


def _cpu_count() -> int:
    # os.cpu_count() reports host cores on Cloud Run (10 for 8 vCPU); the cgroup quota is the real limit.
    available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    quota = cpus_from_quota(CGROUP_CPU_MAX.read_text()) if CGROUP_CPU_MAX.exists() else None
    return min(available, quota) if quota else available


def _temp_directory() -> tuple[Path, bool]:
    if SCRATCH_DIR.is_dir() and os.access(SCRATCH_DIR, os.W_OK):
        return SCRATCH_DIR / "duckdb_tmp", True
    return Path("/tmp/duckdb_tmp"), False


def runtime_settings() -> RuntimeSettings:
    temp_dir, on_ssd = _temp_directory()
    return RuntimeSettings(
        memory_limit_gb=max(1, int(_total_memory_bytes() * MEMORY_FRACTION / 1024**3)),
        threads=_cpu_count(),
        temp_directory=str(temp_dir),
        temp_on_local_ssd=on_ssd,
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        gcs_grpc=GCS_GRPC,
    )


def session_sql(settings: RuntimeSettings) -> tuple[str, ...]:
    project = f", PROJECT_ID '{settings.project_id}'" if settings.project_id else ""
    return (
        "LOAD gcs",
        # Must be set before the first GCS call: the client is built once per process.
        f"SET gcs_enable_grpc = {str(settings.gcs_grpc).lower()}",
        f"CREATE OR REPLACE SECRET duckless_gcp (TYPE GCP, PROVIDER credential_chain{project})",
        f"SET memory_limit = '{settings.memory_limit_gb}GB'",
        f"SET threads = {settings.threads}",
        f"SET temp_directory = '{settings.temp_directory}'",
        # Big COPY / aggregations don't need input order; lowers memory pressure.
        "SET preserve_insertion_order = false",
        "SET enable_progress_bar = false",
    )


def connect(database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    settings = runtime_settings()
    Path(settings.temp_directory).mkdir(parents=True, exist_ok=True)

    # No autoinstall: the image ships gcs + tpch, and httpfs must never take over gs://.
    con = duckdb.connect(database, config={"autoinstall_known_extensions": False})
    for statement in session_sql(settings):
        con.sql(statement)

    log("duckdb_connected", duckdb_version=duckdb.__version__, python=sys.version.split()[0], **asdict(settings))
    return con
