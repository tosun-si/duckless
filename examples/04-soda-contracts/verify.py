"""Verify a Soda v4 data contract on a raw file, with DuckDB on a DuckLess machine.

    duckless exec --image <your-image> -m n2-standard-8 \\
      -e SOURCE=gs://$DUCKLESS_BUCKET/examples/raw/validation/customers.csv \\
      -e CONTRACT=gs://$DUCKLESS_BUCKET/examples/soda/contract.yml \\
      -- python /usr/local/src/app/verify.py

Soda reads the data through the job's DuckDB connection (`duckless_runtime.connect()`), so the
checks run as SQL in DuckDB, on the machine, next to GCS: nothing is loaded into a warehouse
first. The CSV is read with the contract's column types; lines that do not fit are counted by the
CSV reader. The results go to GCS; the job fails when the contract fails.
"""

import json
import os
import tempfile
import time
from collections.abc import Mapping

import yaml
from duckless_runtime import connect, log

DATA_SOURCE = "duckless"


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def table_name(contract: Mapping) -> str:
    """`duckless/main/customers` -> `customers`: the table the contract checks."""
    data_source, _, table = contract["dataset"].partition("/main/")
    if data_source != DATA_SOURCE or not table.isidentifier():
        raise ValueError(f"dataset must be '{DATA_SOURCE}/main/<table>', not '{contract['dataset']}'")
    return table


def read_csv_sql(contract: Mapping, source: str) -> str:
    """The CSV read with the contract's columns and types; rejected lines go to reject_errors."""
    columns = {c["name"]: c["data_type"] for c in contract["columns"]}
    if not all(columns.values()):
        raise ValueError("every column of the contract needs a data_type: it is the schema the file is read with")
    spec = "{" + ", ".join(f"{literal(n)}: {literal(t)}" for n, t in columns.items()) + "}"
    return (
        f"CREATE TABLE {table_name(contract)} AS SELECT * FROM read_csv("
        f"{literal(source)}, header = true, columns = {spec}, store_rejects = true)"
    )


def check_rows(result) -> list[dict]:
    return [
        {
            "check": check.check.name,
            "type": check.check.type,
            "column": getattr(check.check, "column_name", None),
            "outcome": check.outcome.name,
            "metrics": dict(check.diagnostic_metric_values or {}),
        }
        for verification in result.contract_verification_results
        for check in verification.check_results
    ]


def main() -> None:
    # Imported here: the pure functions above are tested without Soda installed.
    from soda_core.contracts.api.verify_api import verify_contract_locally
    from soda_duckdb.common.data_sources.duckdb_data_source import DuckDBDataSourceImpl
    from soda_duckdb.common.data_sources.duckdb_data_source_connection import DuckDBDataSource

    con = connect()
    contract_text = con.sql(f"SELECT content FROM read_text({literal(os.environ['CONTRACT'])})").fetchone()[0]
    contract = yaml.safe_load(contract_text)
    started = time.perf_counter()
    con.sql(read_csv_sql(contract, os.environ["SOURCE"]))
    rejected = con.sql("SELECT count(*) FROM reject_errors").fetchone()[0]
    loaded_in = round(time.perf_counter() - started, 2)

    source = DuckDBDataSourceImpl(
        data_source_model=DuckDBDataSource.model_validate(
            # A cursor: a second connection to the same database, which Soda closes when it is done.
            {"type": "duckdb", "name": DATA_SOURCE, "connection": {"duckdb_connection": con.cursor()}}
        )
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write(contract_text)  # Soda reads contracts from files
    started = time.perf_counter()
    result = verify_contract_locally(data_sources=[source], contract_file_path=f.name)
    verified_in = round(time.perf_counter() - started, 2)
    if result.has_errors:
        raise RuntimeError(f"the contract could not be verified: {result.get_errors_str()}")

    checks = check_rows(result)
    report = {"source": os.environ["SOURCE"], "unparsable_lines": rejected, "passed": result.is_ok, "checks": checks}
    out = os.environ.get("REPORT") or f"gs://{os.environ['DUCKLESS_BUCKET']}/examples/soda/report.json"
    con.sql(f"COPY (SELECT {literal(json.dumps(report, default=str))}::JSON AS report) TO {literal(out)}")
    for check in checks:
        log("soda_check", **check)
    log(
        "soda_contract",
        passed=result.is_ok,
        unparsable_lines=rejected,
        loaded_in_seconds=loaded_in,
        verified_in_seconds=verified_in,
        report=out,
    )
    if not result.is_ok:
        failed = [c["check"] for c in checks if c["outcome"] == "FAILED"]
        raise SystemExit(f"contract failed: {', '.join(failed)} (see {out})")


if __name__ == "__main__":
    main()
