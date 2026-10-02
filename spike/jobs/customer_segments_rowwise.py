"""Stand-in for a typical pure-Python Cloud Run job, rewritten on DuckLess.

Before: stream lineitem/orders files chunk by chunk to stay under Cloud Run's
memory cap, hand-roll the aggregation in Python. After: DuckDB does the heavy
join + aggregation (spilling to local SSD if needed), Python keeps the business
rule that doesn't fit in SQL.
"""

import os

from duckless_runtime import connect, log

bucket, sf, job_id = os.environ["DUCKLESS_BUCKET"], os.environ["TPCH_SF"], os.environ["DUCKLESS_JOB_ID"]
data = f"gs://{bucket}/tpch/sf{sf}"


def segment(revenue: float, orders: int, days_since_last: int) -> str:
    """Business rule owned by the team, unit-testable in plain Python."""
    if days_since_last > 365:
        return "churned"
    if revenue > 2_000_000 and orders > 20:
        return "key_account"
    if revenue / max(orders, 1) > 150_000:
        return "big_basket"
    return "regular"


con = connect()
con.create_function("segment", segment, ["DOUBLE", "BIGINT", "BIGINT"], "VARCHAR")

customers = con.sql(f"""
    SELECT o.o_custkey AS custkey,
           sum(l.l_extendedprice * (1 - l.l_discount)) AS revenue,
           count(DISTINCT o.o_orderkey)                 AS orders,
           date_diff('day', max(o.o_orderdate), DATE '1998-08-02') AS days_since_last
    FROM read_parquet('{data}/lineitem/*.parquet') l
    JOIN read_parquet('{data}/orders/*.parquet') o ON l.l_orderkey = o.o_orderkey
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
