"""Stand-in for a typical pure-Python Cloud Run job, rewritten on DuckLess.

Before: stream lineitem/orders files chunk by chunk to stay under Cloud Run's
memory cap, hand-roll the aggregation in Python. After: DuckDB does the heavy
join + aggregation (spilling to local SSD if needed), Python keeps the business
rule that doesn't fit in SQL.

The rule is vectorized (numpy over Arrow batches of ~2k rows): a row-wise Python
UDF runs at ~8k rows/s single-threaded and dominates the whole job
(see customer_segments_rowwise.py).
"""

import os

import numpy as np
import pyarrow as pa

from duckless_runtime import connect, log

bucket, sf, job_id = os.environ["DUCKLESS_BUCKET"], os.environ["TPCH_SF"], os.environ["DUCKLESS_JOB_ID"]
data = f"gs://{bucket}/tpch/sf{sf}"


def segment(revenue: np.ndarray, orders: np.ndarray, days_since_last: np.ndarray) -> np.ndarray:
    """Business rule owned by the team: pure, vectorized, unit-testable with plain arrays."""
    return np.select(
        [
            days_since_last > 365,
            (revenue > 2_000_000) & (orders > 20),
            revenue / np.maximum(orders, 1) > 150_000,
        ],
        ["churned", "key_account", "big_basket"],
        default="regular",
    )


def segment_arrow(revenue: pa.Array, orders: pa.Array, days_since_last: pa.Array) -> pa.Array:
    return pa.array(segment(*(a.to_numpy(zero_copy_only=False) for a in (revenue, orders, days_since_last))))


con = connect()
con.create_function("segment", segment_arrow, ["DOUBLE", "BIGINT", "BIGINT"], "VARCHAR", type="arrow")

customers = con.sql(f"""
    WITH order_revenue AS (
        SELECT l_orderkey, sum(l_extendedprice * (1 - l_discount)) AS revenue
        FROM read_parquet('{data}/lineitem/*.parquet')
        GROUP BY ALL
    )
    SELECT o.o_custkey AS custkey,
           sum(r.revenue) AS revenue,
           count(*) AS orders,
           date_diff('day', max(o.o_orderdate), DATE '1998-08-02') AS days_since_last
    FROM read_parquet('{data}/orders/*.parquet') o
    JOIN order_revenue r ON r.l_orderkey = o.o_orderkey
    GROUP BY ALL
""")

con.sql(f"""
    COPY (
        SELECT custkey, revenue, orders, days_since_last, segment(revenue, orders, days_since_last) AS segment
        FROM customers
    ) TO 'gs://{bucket}/out/{job_id}/customer_segments' (FORMAT parquet, FILE_SIZE_BYTES '256MB', OVERWRITE)
""")

summary = con.sql(f"""
    SELECT segment, count(*) AS customers
    FROM read_parquet('gs://{bucket}/out/{job_id}/customer_segments/*.parquet')
    GROUP BY ALL ORDER BY customers DESC
""").fetchall()
log("segments_written", summary=summary)
