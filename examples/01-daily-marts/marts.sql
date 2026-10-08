-- Daily marts: raw Parquet on GCS in, two marts out, in plain SQL.
--   duckless run examples/01-daily-marts/marts.sql -m n2-standard-8
-- n2-standard-8 fits Cloud Run Jobs, so it starts in about 30 seconds. For SCALE=100 raw data:
--   duckless run examples/01-daily-marts/marts.sql -m n2-highmem-32 --spot

CREATE VIEW nation   AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/examples/raw/nation/*.parquet');
CREATE VIEW customer AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/examples/raw/customer/*.parquet');
CREATE VIEW orders   AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/examples/raw/orders/*.parquet');
CREATE VIEW lineitem AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/examples/raw/lineitem/*.parquet');

-- Revenue per nation and month, partitioned by year: readers that filter on a year only open its files.
COPY (
    SELECT
        n.n_name                                         AS nation,
        year(o.o_orderdate)                              AS year,
        date_trunc('month', o.o_orderdate)               AS month,
        count(DISTINCT o.o_orderkey)                     AS orders,
        sum(l.l_extendedprice * (1 - l.l_discount))      AS revenue
    FROM lineitem l
    JOIN orders o   ON o.o_orderkey = l.l_orderkey
    JOIN customer c ON c.c_custkey = o.o_custkey
    JOIN nation n   ON n.n_nationkey = c.c_nationkey
    GROUP BY ALL
) TO 'gs://${DUCKLESS_BUCKET}/examples/marts/revenue_by_nation_month'
  (FORMAT parquet, PARTITION_BY (year), OVERWRITE);

-- Customer lifetime value, one row per customer: written by every thread in parallel.
COPY (
    SELECT
        c.c_custkey                                      AS customer_id,
        c.c_name                                         AS customer,
        c.c_mktsegment                                   AS segment,
        count(*)                                         AS orders,
        sum(o.o_totalprice)                              AS lifetime_value,
        max(o.o_orderdate)                               AS last_order
    FROM orders o
    JOIN customer c ON c.c_custkey = o.o_custkey
    GROUP BY ALL
) TO 'gs://${DUCKLESS_BUCKET}/examples/marts/customer_value'
  (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB', OVERWRITE);

-- Shown by `duckless result <job-id>`: a quick check of what was just written.
SELECT nation, round(sum(revenue) / 1e6, 1) AS revenue_millions
FROM read_parquet('gs://${DUCKLESS_BUCKET}/examples/marts/revenue_by_nation_month/*/*.parquet', hive_partitioning = true)
WHERE year = 1997
GROUP BY ALL
ORDER BY revenue_millions DESC
LIMIT 5;
