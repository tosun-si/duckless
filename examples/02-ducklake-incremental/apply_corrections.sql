-- Corrections that arrive days later, as newline-delimited JSON: cancel some orders, erase a
-- customer's orders. Plain Parquet files would need rewriting; a DuckLake table takes them as is.
--   duckless run examples/02-ducklake-incremental/apply_corrections.sql -m n2-standard-4

CREATE TEMP TABLE corrections AS
SELECT * FROM read_json('gs://${DUCKLESS_BUCKET}/examples/raw/corrections/*.ndjson');

-- Both kinds in one transaction: one snapshot, all or nothing. (A single MERGE INTO with an UPDATE
-- and a DELETE branch would read better, but DuckLake accepts one action per MERGE for now.)
BEGIN TRANSACTION;
UPDATE lake.orders SET o_orderstatus = 'C'
WHERE o_orderkey IN (SELECT o_orderkey FROM corrections WHERE action = 'cancel');
DELETE FROM lake.orders
WHERE o_orderkey IN (SELECT o_orderkey FROM corrections WHERE action = 'erase');
COMMIT;

SELECT
  (SELECT count(*) FROM lake.orders WHERE o_orderstatus = 'C')                  AS cancelled_orders,
  (SELECT count(*) FROM corrections WHERE action = 'erase')                     AS erased_orders,
  (SELECT count(*) FROM lake.orders)                                            AS orders_now;
