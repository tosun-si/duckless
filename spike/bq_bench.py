# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-bigquery>=3.25", "duckdb==1.5.6"]
# ///
"""Spike: same TPC-H queries on BigQuery, over the same Parquet files.

    uv run bq_bench.py --sf 100 --mode external   # external tables on the GCS Parquet
    uv run bq_bench.py --sf 100 --mode native     # loaded into BigQuery storage (load is free)

On-demand pricing: bytes billed x --price-per-tib (check the region's current price).
"""

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
from google.cloud import bigquery

BIG_TABLES = ("lineitem", "orders", "customer", "partsupp", "part")
SMALL_TABLES = ("supplier", "nation", "region")  # single Parquet files, see jobs/tpch_gen.sql
QUERY_NUMBERS = (1, 9, 18, 21)


@dataclass(frozen=True)
class Run:
    name: str
    seconds: float
    bytes_billed: int
    slot_seconds: float
    usd: float


# ---------- pure ----------

def table_uri(bucket: str, sf: int, table: str) -> str:
    base = f"gs://{bucket}/tpch/sf{sf}/{table}"
    return f"{base}/*.parquet" if table in BIG_TABLES else base


def benchmark_queries() -> tuple[tuple[str, str], ...]:
    """The exact DuckDB TPC-H texts (standard SQL, run as-is on BigQuery) + the spike's scan and write."""
    con = duckdb.connect()
    con.sql("INSTALL tpch; LOAD tpch")
    tpch = tuple(
        (f"q{n}", q) for n, q in con.execute(
            "SELECT query_nr, query FROM tpch_queries() WHERE query_nr IN (SELECT unnest(?))", [list(QUERY_NUMBERS)]
        ).fetchall()
    )
    return (
        ("full_scan", "SELECT count(*), BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) FROM lineitem AS t"),
        *tpch,
        ("aggregate_write", """
            CREATE OR REPLACE TABLE revenue_by_month AS
            SELECT l_returnflag, l_linestatus, DATE_TRUNC(l_shipdate, MONTH) AS ship_month,
                   count(*) AS lines, sum(l_extendedprice * (1 - l_discount)) AS revenue
            FROM lineitem GROUP BY ALL"""),
    )


def to_run(name: str, job: bigquery.QueryJob, price_per_tib: float) -> Run:
    billed = job.total_bytes_billed or 0
    return Run(
        name=name,
        seconds=round((job.ended - job.started).total_seconds(), 2),
        bytes_billed=billed,
        slot_seconds=round((job.slot_millis or 0) / 1000, 1),
        usd=round(billed / 1024**4 * price_per_tib, 4),
    )


# ---------- effects ----------

def ensure_tables(client: bigquery.Client, dataset: str, bucket: str, sf: int, mode: str) -> None:
    client.create_dataset(bigquery.Dataset(dataset), exists_ok=True)
    for table in (*BIG_TABLES, *SMALL_TABLES):
        ref = f"{dataset}.{table}"
        if mode == "external":
            client.create_table(external_table(ref, table_uri(bucket, sf, table)), exists_ok=True)
        else:
            config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            client.load_table_from_uri(table_uri(bucket, sf, table), ref, job_config=config).result()


def external_table(ref: str, uri: str) -> bigquery.Table:
    external = bigquery.ExternalConfig("PARQUET")
    external.source_uris = [uri]
    table = bigquery.Table(ref)
    table.external_data_configuration = external
    return table


def run_query(client: bigquery.Client, dataset: str, name: str, sql: str, price_per_tib: float) -> Run:
    config = bigquery.QueryJobConfig(use_query_cache=False, default_dataset=dataset)
    job = client.query(sql, job_config=config)
    job.result()
    run = to_run(name, job, price_per_tib)
    print(json.dumps(asdict(run)), flush=True)
    return run


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=os.environ.get("DUCKLESS_PROJECT"))
    p.add_argument("--region", default=os.environ.get("DUCKLESS_REGION", "europe-west1"))
    p.add_argument("--bucket", default=os.environ.get("DUCKLESS_BUCKET"))
    p.add_argument("--sf", type=int, default=100)
    p.add_argument("--mode", choices=("external", "native"), default="external")
    p.add_argument("--price-per-tib", type=float, default=6.25)
    args = p.parse_args()

    client = bigquery.Client(project=args.project, location=args.region)
    dataset = f"{args.project}.duckless_spike_sf{args.sf}_{args.mode}"
    started = time.time()
    ensure_tables(client, dataset, args.bucket, args.sf, args.mode)
    setup_s = round(time.time() - started, 1)

    runs = tuple(run_query(client, dataset, name, sql, args.price_per_tib) for name, sql in benchmark_queries())
    report = {
        "engine": "bigquery", "mode": args.mode, "sf": args.sf, "setup_seconds": setup_s,
        "runs": [asdict(r) for r in runs],
        "total_seconds": round(sum(r.seconds for r in runs), 2),
        "total_usd": round(sum(r.usd for r in runs), 4),
    }
    out = Path(__file__).parent / "results" / f"bq-sf{args.sf}-{args.mode}.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    main()
