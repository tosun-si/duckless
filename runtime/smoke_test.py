"""Run inside the built image: the extensions the runner relies on are baked in, httpfs is not.

docker run --rm --entrypoint python <image> /usr/local/src/app/smoke_test.py
"""

import subprocess

import duckdb
from duckless_runtime import (
    copy_to_parquet_sql,
    cpus_from_quota,
    open_files_target,
    raise_open_files_limit,
    runtime_settings,
    session_sql,
)
from duckless_runtime.lake import PROXY, attach_sql, lake_settings

con = duckdb.connect(config={"autoinstall_known_extensions": False})
installed = dict(con.sql("SELECT extension_name, installed FROM duckdb_extensions()").fetchall())

assert installed.get("gcs"), "gcs community extension must be baked in"
assert installed.get("tpch"), "tpch extension must be baked in"
assert installed.get("ducklake") and installed.get("postgres_scanner"), "DuckLake catalog extensions must be baked in"
assert all(installed.get(e) for e in ("excel", "avro", "iceberg")), "file format extensions must be baked in"
assert not installed.get("httpfs"), "httpfs must not be installed: it would take over gs://"

for statement in session_sql(runtime_settings()):
    if "SECRET" not in statement:  # needs GCP credentials
        con.sql(statement)

# A job's last SELECT is fetched in Python: TIMESTAMPTZ values need pytz.
assert con.sql("SELECT TIMESTAMPTZ '2026-10-08 10:00:00+00' AS t").fetchone()[0].year == 2026
assert cpus_from_quota("800000 100000") == 8
assert open_files_target(1024, 524288) == 65536 and open_files_target(1024, 4096) == 4096
assert raise_open_files_limit() >= 1024
assert "PER_THREAD_OUTPUT" in copy_to_parquet_sql("SELECT 1", "gs://b/x")

assert subprocess.run([PROXY, "--version"], capture_output=True, text=True, check=True).stdout.startswith(
    "cloud-sql-proxy"
)
lake = lake_settings(
    {
        "DUCKLESS_DUCKLAKE_INSTANCE": "p:r:i",
        "DUCKLESS_DUCKLAKE_DATA_PATH": "gcss://b/lake/",
        "DUCKLESS_DUCKLAKE_USER": "runner@p.iam.gserviceaccount.com",
    }
)
assert lake is not None and lake.user == "runner@p.iam" and "password" not in attach_sql(lake)
assert lake_settings({}) is None
for extension in ("excel", "avro", "iceberg"):
    con.sql(f"LOAD {extension}")
con.sql("COPY (SELECT 1 AS a) TO '/tmp/smoke.xlsx' (FORMAT xlsx, HEADER true)")
assert con.sql("SELECT a FROM read_xlsx('/tmp/smoke.xlsx')").fetchone()[0] == 1
print(f"smoke ok: duckdb {duckdb.__version__}, gcs, tpch, ducklake, postgres, excel, avro, iceberg, proxy, no httpfs")
