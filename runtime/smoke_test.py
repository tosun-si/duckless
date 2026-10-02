"""Run inside the built image: the extensions the runner relies on are baked in, httpfs is not.

    docker run --rm --entrypoint python <image> /usr/local/src/app/smoke_test.py
"""

import duckdb

from duckless_runtime import copy_to_parquet_sql, cpus_from_quota, session_sql, runtime_settings

con = duckdb.connect(config={"autoinstall_known_extensions": False})
installed = dict(con.sql("SELECT extension_name, installed FROM duckdb_extensions()").fetchall())

assert installed.get("gcs"), "gcs community extension must be baked in"
assert installed.get("tpch"), "tpch extension must be baked in"
assert not installed.get("httpfs"), "httpfs must not be installed: it would take over gs://"

for statement in session_sql(runtime_settings()):
    if "SECRET" not in statement:  # needs GCP credentials
        con.sql(statement)

assert cpus_from_quota("800000 100000") == 8
assert "PER_THREAD_OUTPUT" in copy_to_parquet_sql("SELECT 1", "gs://b/x")
print(f"smoke ok: duckdb {duckdb.__version__}, extensions gcs + tpch, no httpfs")
