"""Runner entrypoint.

    python -m duckless_runtime sql <gs://…/job.sql | path>
    python -m duckless_runtime py  <gs://…/job.py  | path> [args…]

Env: DUCKLESS_JOB_ID, DUCKLESS_METRICS_URI (gs://…/metrics.json).
"""

import json
import os
import resource
import runpy
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from string import Template

import duckdb

from duckless_runtime import connect, log, runtime_settings

PREVIEW_ROWS = 20
GCS_SCHEMES = ("gs://", "gcs://", "gcss://")
ROW_STATEMENTS = frozenset({duckdb.StatementType.SELECT, duckdb.StatementType.PRAGMA})


# ---------- pure ----------

def render(source: str, env: Mapping[str, str]) -> str:
    """${VAR} placeholders come from the job env; $1-style params are left alone."""
    return Template(source).safe_substitute(env)


def to_gb(n_bytes: float) -> float:
    return round(n_bytes / 1024**3, 2)


def peak_rss_bytes(ru_maxrss: int, platform: str) -> int:
    """ru_maxrss is bytes on macOS, KiB on Linux."""
    return ru_maxrss if platform == "darwin" else ru_maxrss * 1024


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else 0


# ---------- effects ----------

def spill_samples(path: Path, stop: threading.Event, every_s: float) -> Iterator[int]:
    while not stop.wait(every_s):
        try:
            yield dir_size(path)
        except OSError:  # a spill file vanished mid-walk
            continue


def watch_peak_spill(path: Path, stop: threading.Event, every_s: float = 1.0) -> Future[int]:
    """Peak size of DuckDB's spill directory, resolved once `stop` is set."""
    return ThreadPoolExecutor(max_workers=1).submit(lambda: max(spill_samples(path, stop, every_s), default=0))


def read_source(con: duckdb.DuckDBPyConnection, uri: str) -> str:
    if uri.startswith(GCS_SCHEMES):
        return con.execute("SELECT content FROM read_text(?)", [uri]).fetchone()[0]
    return Path(uri).read_text()


def run_statement(con: duckdb.DuckDBPyConnection, index: int, statement) -> dict:
    query = statement.query.strip()
    started = time.perf_counter()
    con.execute(query)
    preview = (
        tuple(tuple(map(str, row)) for row in con.fetchmany(PREVIEW_ROWS)) if statement.type in ROW_STATEMENTS else ()
    )
    step = {"index": index, "type": statement.type.name, "sql": query[:200], "preview": preview,
            "seconds": round(time.perf_counter() - started, 3)}
    log("statement_done", **step)
    return step


def run_sql(con: duckdb.DuckDBPyConnection, source: str) -> dict:
    statements = con.extract_statements(render(source, os.environ))
    return {"steps": tuple(run_statement(con, i, s) for i, s in enumerate(statements))}


def run_python(source: str, args: list[str]) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(source)
    sys.argv = [f.name, *args]
    runpy.run_path(f.name, run_name="__main__")
    return {}


def run_job(con: duckdb.DuckDBPyConnection, mode: str, uri: str, args: list[str]) -> dict:
    try:
        source = read_source(con, uri)
        outcome = run_sql(con, source) if mode == "sql" else run_python(source, args)
        return {"status": "succeeded", **outcome}
    except Exception as e:  # noqa: BLE001 — report any failure, the caller exits non-zero
        error = f"{type(e).__name__}: {e}"
        log("job_failed", severity="ERROR", error=error)
        return {"status": "failed", "error": error}


def write_metrics(con: duckdb.DuckDBPyConnection, uri: str | None, metrics: dict) -> None:
    if not uri:
        return
    local = Path(tempfile.gettempdir()) / "duckless_metrics.json"
    local.write_text(json.dumps(metrics, default=str))
    con.execute(f"COPY (SELECT * FROM read_json('{local}')) TO '{uri}' (FORMAT json)")


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("sql", "py"):
        print(__doc__)
        return 2
    mode, uri, args = argv[1], argv[2], argv[3:]

    settings = runtime_settings()
    started = time.time()
    con = connect()
    stop = threading.Event()
    peak_spill = watch_peak_spill(Path(settings.temp_directory), stop)

    outcome = run_job(con, mode, uri, args)

    stop.set()
    metrics = {
        "job_id": os.environ.get("DUCKLESS_JOB_ID"),
        "mode": mode,
        "source": uri,
        "settings": asdict(settings),
        "started_at": started,
        **outcome,
        "seconds": round(time.time() - started, 3),
        "peak_rss_gb": to_gb(peak_rss_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, sys.platform)),
        "peak_spill_gb": to_gb(peak_spill.result()),
    }
    log("job_done", **{k: v for k, v in metrics.items() if k != "steps"})
    write_metrics(con, os.environ.get("DUCKLESS_METRICS_URI"), metrics)
    return 0 if outcome["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
