-- Generates TPC-H at scale ${TPCH_SF} and writes it as Parquet to GCS.
-- Small tables (supplier, nation, region) are single files, the others directories.
-- The database lives on local SSD: dbgen at SF100 does not fit comfortably in RAM next to the COPY buffers.
ATTACH '/mnt/disks/scratch/tpch.duckdb' AS tpch;
USE tpch;
CALL dbgen(sf = ${TPCH_SF});

COPY lineitem TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/lineitem' (FORMAT parquet, FILE_SIZE_BYTES '256MB', OVERWRITE);
COPY orders   TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/orders'   (FORMAT parquet, FILE_SIZE_BYTES '256MB', OVERWRITE);
COPY customer TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/customer' (FORMAT parquet, FILE_SIZE_BYTES '256MB', OVERWRITE);
COPY partsupp TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/partsupp' (FORMAT parquet, FILE_SIZE_BYTES '256MB', OVERWRITE);
COPY part     TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/part'     (FORMAT parquet, FILE_SIZE_BYTES '256MB', OVERWRITE);
COPY supplier TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/supplier' (FORMAT parquet, OVERWRITE);
COPY nation   TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/nation'   (FORMAT parquet, OVERWRITE);
COPY region   TO 'gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/region'   (FORMAT parquet, OVERWRITE);

SELECT count(*) AS files, sum(size) / 1e9 AS gb FROM read_blob('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/*/*.parquet');
