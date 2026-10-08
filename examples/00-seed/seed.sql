-- Raw data for the examples: TPC-H generated on the runner, written as Parquet to the work bucket.
--   duckless run examples/00-seed/seed.sql -m n2-standard-4 -e SCALE=1
-- SCALE=1 is about 1 GB of raw data; SCALE=100 needs a bigger machine (n2-highmem-32 --spot).

CALL dbgen(sf = ${SCALE});

COPY nation   TO 'gs://${DUCKLESS_BUCKET}/examples/raw/nation/nation.parquet' (FORMAT parquet);
COPY customer TO 'gs://${DUCKLESS_BUCKET}/examples/raw/customer' (FORMAT parquet, PER_THREAD_OUTPUT, OVERWRITE);
COPY orders   TO 'gs://${DUCKLESS_BUCKET}/examples/raw/orders'   (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB', OVERWRITE);
COPY lineitem TO 'gs://${DUCKLESS_BUCKET}/examples/raw/lineitem' (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB', OVERWRITE);

-- What an upstream system drops for example 02: one CSV folder per day of orders...
COPY (SELECT * FROM orders WHERE o_orderdate BETWEEN DATE '1998-07-01' AND DATE '1998-07-07')
  TO 'gs://${DUCKLESS_BUCKET}/examples/raw/orders_daily' (FORMAT csv, HEADER, PARTITION_BY (o_orderdate), OVERWRITE);

-- ...and, later, corrections as newline-delimited JSON: cancelled orders, and a customer who asked
-- to be erased (every order of theirs on 1998-07-01 and 1998-07-02).
COPY (
    SELECT o_orderkey, 'cancel' AS action, TIMESTAMP '1998-07-05 09:00:00' AS reported_at
    FROM orders
    WHERE o_orderdate IN (DATE '1998-07-01', DATE '1998-07-02') AND o_orderkey % 10 = 3
    UNION ALL
    SELECT o_orderkey, 'erase' AS action, TIMESTAMP '1998-07-06 14:30:00' AS reported_at
    FROM orders
    WHERE o_custkey = (
        SELECT min(o_custkey) FROM orders WHERE o_orderdate IN (DATE '1998-07-01', DATE '1998-07-02')
    ) AND o_orderdate IN (DATE '1998-07-01', DATE '1998-07-02')
) TO 'gs://${DUCKLESS_BUCKET}/examples/raw/corrections/1998-07-06.ndjson' (FORMAT json);

SELECT
  (SELECT count(*) FROM orders)   AS orders,
  (SELECT count(*) FROM lineitem) AS lineitems,
  (SELECT count(*) FROM orders WHERE o_orderdate BETWEEN DATE '1998-07-01' AND DATE '1998-07-07') AS daily_orders,
  (SELECT count(*) FROM read_json('gs://${DUCKLESS_BUCKET}/examples/raw/corrections/*.ndjson')) AS corrections;
