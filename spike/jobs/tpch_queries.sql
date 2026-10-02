-- Reads TPC-H Parquet straight from GCS (no local copy), runs the heavy queries, writes an aggregate back.
CREATE VIEW lineitem AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/lineitem/*.parquet');
CREATE VIEW orders   AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/orders/*.parquet');
CREATE VIEW customer AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/customer/*.parquet');
CREATE VIEW partsupp AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/partsupp/*.parquet');
CREATE VIEW part     AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/part/*.parquet');
CREATE VIEW supplier AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/supplier');
CREATE VIEW nation   AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/nation');
CREATE VIEW region   AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/region');

-- Raw read throughput: full scan of lineitem, every column.
SELECT count(*), sum(hash(COLUMNS(*))) FROM lineitem;

-- Q1 (scan + aggregate), Q9 and Q18 (big joins, the ones that spill), Q21 (semi/anti joins).
PRAGMA tpch(1);
PRAGMA tpch(9);
PRAGMA tpch(18);
PRAGMA tpch(21);

-- Success criterion: read → aggregate → write back to GCS, partitioned.
COPY (
    SELECT l_returnflag, l_linestatus, date_trunc('month', l_shipdate) AS ship_month,
           count(*) AS lines, sum(l_extendedprice * (1 - l_discount)) AS revenue
    FROM lineitem
    GROUP BY ALL
) TO 'gs://${DUCKLESS_BUCKET}/out/${DUCKLESS_JOB_ID}/revenue_by_month' (FORMAT parquet, PARTITION_BY (l_returnflag), OVERWRITE);
