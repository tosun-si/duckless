"""Read a BigQuery table, apply business logic written in Python, write the result to GCS.

    duckless run examples/05-bigquery-to-python/score_orders.py -m n2-highmem-16 --on batch \\
      -e BQ_DATASET=duckless_examples

The logic is plain NumPy, registered as a vectorized (Arrow) function: DuckDB hands it whole
columns of thousands of rows at a time, on every thread, so the Python runs at NumPy speed
instead of once per row.
"""

import os

import numpy as np
import pyarrow as pa
from duckdb.sqltypes import DOUBLE, VARCHAR
from duckless_runtime import connect, copy_to_parquet, log, read_bigquery

# How much each order priority weighs in the score: business knowledge, kept in Python.
PRIORITY_WEIGHT = {"1-URGENT": 1.5, "2-HIGH": 1.25, "3-MEDIUM": 1.0, "4-NOT SPECIFIED": 0.9, "5-LOW": 0.8}


def order_score(price: pa.Array, priority: pa.Array) -> pa.Array:
    """Value score of an order: log-scaled price, weighted by its priority. Pure, testable alone."""
    prices = price.to_numpy(zero_copy_only=False)
    weights = np.array([PRIORITY_WEIGHT.get(p, 1.0) for p in priority.to_pylist()])
    return pa.array(np.round(np.log1p(prices) * weights, 3))


def main() -> None:
    con = connect()
    table = f"{os.environ['GOOGLE_CLOUD_PROJECT']}.{os.environ['BQ_DATASET']}.orders"

    # Only these columns and these rows leave BigQuery (Storage Read API, parallel streams).
    orders = read_bigquery(
        con,
        table,
        columns=["o_orderkey", "o_custkey", "o_orderdate", "o_totalprice", "o_orderpriority"],
        where="o_orderdate >= DATE '1995-01-01'",
    )
    orders.create_view("orders")
    con.create_function("order_score", order_score, [DOUBLE, VARCHAR], DOUBLE, type="arrow")

    out = f"gs://{os.environ['DUCKLESS_BUCKET']}/examples/bigquery/scored_orders"
    copy_to_parquet(
        con,
        "SELECT o_orderkey, o_custkey, o_orderdate, order_score(o_totalprice::DOUBLE, o_orderpriority) AS score "
        "FROM orders",
        out,
    )
    rows, average = con.sql(f"SELECT count(*), round(avg(score), 3) FROM read_parquet('{out}/*.parquet')").fetchone()
    log("orders_scored", rows=rows, average_score=average, written_to=out)


if __name__ == "__main__":
    main()
