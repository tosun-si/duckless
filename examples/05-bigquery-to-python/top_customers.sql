-- Read a BigQuery table in SQL: only the two columns and the rows of 1997 leave BigQuery.
--   duckless run examples/05-bigquery-to-python/top_customers.sql -m n2-standard-4 -e BQ_DATASET=duckless_examples

SELECT o_custkey AS customer_id, round(sum(o_totalprice), 2) AS spent_1997
FROM bigquery_scan(
    '${GOOGLE_CLOUD_PROJECT}.${BQ_DATASET}.orders',
    filter = 'o_orderdate BETWEEN DATE ''1997-01-01'' AND DATE ''1997-12-31'''
)
GROUP BY ALL
ORDER BY spent_1997 DESC
LIMIT 10;
