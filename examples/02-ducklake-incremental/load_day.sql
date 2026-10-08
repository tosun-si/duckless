-- Load one day of orders, dropped upstream as CSV, into a DuckLake table. Re-runnable.
-- Needs an installation with a catalog (`duckless init --ducklake`): every job has it as `lake`.
--   duckless run examples/02-ducklake-incremental/load_day.sql -m n2-standard-4 -e LOAD_DATE=1998-07-01

CREATE TABLE IF NOT EXISTS lake.orders AS
SELECT * FROM read_csv('gs://${DUCKLESS_BUCKET}/examples/raw/orders_daily/*/*.csv', hive_partitioning = true)
LIMIT 0;

-- Delete then insert the day in one transaction: one snapshot, and a re-run replaces the day
-- instead of loading it twice.
BEGIN TRANSACTION;
DELETE FROM lake.orders WHERE o_orderdate = DATE '${LOAD_DATE}';
INSERT INTO lake.orders
SELECT * FROM read_csv('gs://${DUCKLESS_BUCKET}/examples/raw/orders_daily/o_orderdate=${LOAD_DATE}/*.csv', hive_partitioning = true);
COMMIT;

SELECT o_orderdate, count(*) AS orders
FROM lake.orders
GROUP BY ALL
ORDER BY o_orderdate;
