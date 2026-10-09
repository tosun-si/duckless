"""Reading BigQuery from a job, through DuckDB's `bigquery` extension (BigQuery Storage Read API).

The extension reads a table as Arrow, in as many parallel streams as DuckDB has threads, with
the selected columns and the filter applied by BigQuery: only what the job needs leaves
BigQuery, billed at Storage Read API prices (about six times less than an on-demand query).
It uses the job's service account, like GCS.
"""

import duckdb

IDENTIFIER_PART = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def table_path(table: str) -> str:
    """`project.dataset.table` (`project:dataset.table` accepted), checked before it reaches SQL."""
    parts = table.replace(":", ".", 1).split(".")
    if len(parts) != 3 or not all(p and set(p) <= IDENTIFIER_PART for p in parts):
        raise ValueError(f"'{table}' is not a BigQuery table name of the form project.dataset.table")
    return ".".join(parts)


def scan_sql(table: str, columns: list[str] | None = None, where: str | None = None) -> str:
    """SQL reading a BigQuery table; `where` is a BigQuery filter, applied by BigQuery itself."""
    selected = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    options = f", filter = '{where.replace(chr(39), chr(39) * 2)}'" if where else ""
    return f"SELECT {selected} FROM bigquery_scan('{table_path(table)}'{options})"


def read_bigquery(
    con: duckdb.DuckDBPyConnection, table: str, columns: list[str] | None = None, where: str | None = None
) -> duckdb.DuckDBPyRelation:
    """A DuckDB relation over a BigQuery table: query it in SQL, or get Arrow, pandas or Polars from it.

    orders = read_bigquery(con, "my-project.sales.orders", ["order_id", "amount"], "order_date >= '2026-01-01'")
    for batch in orders.fetch_record_batch():   # Arrow batches, never the whole table in memory
        ...
    """
    con.sql("LOAD bigquery")
    return con.sql(scan_sql(table, columns, where))
