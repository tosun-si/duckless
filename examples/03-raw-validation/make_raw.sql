-- A raw customers CSV, the way it arrives from a source system: mostly fine, with a few
-- thousand broken values and a few hundred lines the CSV reader cannot even parse.
--   duckless run examples/03-raw-validation/make_raw.sql -m n2-standard-4 -e ROWS=1000000

COPY (
  SELECT line FROM (
    WITH customers AS (
        SELECT
            id,
            -- duplicate id: every 1,021st customer reuses the previous id
            CASE WHEN id % 1021 = 0 THEN id - 1 ELSE id END                                AS customer_id,
            CASE WHEN id % 1031 = 0 THEN '' ELSE 'Customer ' || id END                     AS name,
            CASE WHEN id % 997 = 0 THEN 'customer' || id || '.example.com'                 -- no @
                 ELSE 'customer' || id || '@example.com' END                               AS email,
            CASE WHEN id % 1019 = 0 THEN 'XX'                                              -- unknown code
                 ELSE ['FR', 'DE', 'ES', 'IT', 'GB', 'US', 'BE', 'NL', 'PT', 'CH'][1 + id % 10] END AS country,
            CASE WHEN id % 1013 = 0 THEN DATE '2099-01-01'                                 -- in the future
                 ELSE DATE '2015-01-01' + CAST(id % 3650 AS INTEGER) END                   AS signup_date,
            CASE WHEN id % 1009 = 0 THEN -(id % 500)                                       -- negative
                 ELSE round((id % 100000) / 7.0, 2) END                                    AS lifetime_value
        FROM range(1, ${ROWS} + 1) AS r(id)
    )
    SELECT 'customer_id,name,email,country,signup_date,lifetime_value' AS line, 0 AS id
    UNION ALL
    SELECT
        CASE
            -- lines the reader cannot parse: a text in a number, an impossible date, an extra column
            WHEN id % 5003 = 0 THEN concat_ws(',', customer_id, name, email, country, signup_date, 'n/a')
            WHEN id % 9001 = 0 THEN concat_ws(',', customer_id, name, email, country, '2024-13-45', lifetime_value)
            WHEN id % 7001 = 0 THEN concat_ws(',', customer_id, name, email, country, signup_date, lifetime_value, 'extra')
            ELSE concat_ws(',', customer_id, name, email, country, signup_date, lifetime_value)
        END,
        id
    FROM customers
  )
  ORDER BY id
) TO 'gs://${DUCKLESS_BUCKET}/examples/raw/validation/customers.csv'
  -- one text column written as is: no quoting, a delimiter that never appears in the lines
  (FORMAT csv, HEADER false, DELIMITER '|', QUOTE '');
