-- The table before the corrections: DuckLake keeps every snapshot.
--   duckless run examples/02-ducklake-incremental/history.sql -m n2-standard-4 -e VERSION=4
-- Pick VERSION from the snapshot list (first statement, in `duckless logs`): the last load
-- before the corrections.

SELECT snapshot_id, snapshot_time::VARCHAR AS snapshot_time, changes::VARCHAR AS changes
FROM ducklake_snapshots('lake')
ORDER BY snapshot_id;

SELECT
  'before' AS state, count(*) AS orders, count(*) FILTER (o_orderstatus = 'C') AS cancelled
FROM lake.orders AT (VERSION => ${VERSION})
UNION ALL
SELECT
  'now', count(*), count(*) FILTER (o_orderstatus = 'C')
FROM lake.orders;
