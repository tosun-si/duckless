"""DuckLake catalog attach: Cloud SQL Postgres reached through the Cloud SQL Auth Proxy.

The proxy runs next to DuckDB with --auto-iam-authn: it logs in as the job's service account
and refreshes the IAM token itself, so jobs longer than the token's hour keep working and no
password ever goes in a DSN (DuckDB prints the whole DSN in its connection errors).
"""

import atexit
import os
import socket
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass

PROXY = os.environ.get("DUCKLESS_PROXY_BIN", "/usr/local/bin/cloud-sql-proxy")
PROXY_PORT = 5432
CATALOG_DB = "ducklake"
SA_SUFFIX = ".gserviceaccount.com"


@dataclass(frozen=True)
class LakeSettings:
    instance: str  # connection name, project:region:instance
    data_path: str  # gcss://bucket/lake/ (gs:// would be served by httpfs, absent on purpose)
    user: str  # Postgres name of the IAM user: the SA email without ".gserviceaccount.com"
    alias: str


def lake_settings(env: Mapping[str, str]) -> LakeSettings | None:
    instance = env.get("DUCKLESS_DUCKLAKE_INSTANCE", "")
    if not instance:
        return None
    return LakeSettings(
        instance=instance,
        data_path=env["DUCKLESS_DUCKLAKE_DATA_PATH"],
        user=env["DUCKLESS_DUCKLAKE_USER"].removesuffix(SA_SUFFIX),
        alias=env.get("DUCKLESS_DUCKLAKE_ALIAS", "lake"),
    )


def proxy_command(lake: LakeSettings, port: int = PROXY_PORT) -> list[str]:
    return [
        PROXY,
        "--private-ip",
        "--auto-iam-authn",
        "--quiet",  # one log line per connection otherwise, and DuckLake opens many
        "--structured-logs",
        f"--port={port}",
        lake.instance,
    ]


def attach_sql(lake: LakeSettings, port: int = PROXY_PORT) -> str:
    # The proxy encrypts the hop to Cloud SQL; the local one stays on loopback.
    dsn = f"ducklake:postgres:host=127.0.0.1 port={port} dbname={CATALOG_DB} user={lake.user} sslmode=disable"
    return f"ATTACH '{dsn}' AS {lake.alias} (DATA_PATH '{lake.data_path}')"


def _wait_port(port: int, proxy: subprocess.Popen, timeout: float = 30) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if proxy.poll() is not None:
            raise RuntimeError(f"cloud-sql-proxy exited with code {proxy.returncode}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return time.perf_counter() - started
        time.sleep(0.1)
    raise TimeoutError(f"cloud-sql-proxy not listening on {port} after {timeout}s")


def start_proxy(lake: LakeSettings, port: int = PROXY_PORT) -> float:
    """Starts the proxy for the rest of the process; returns how long it took to listen."""
    proxy = subprocess.Popen(proxy_command(lake, port))
    atexit.register(proxy.terminate)
    return _wait_port(port, proxy)
