-- GCS write throughput: the same lineitem (SF${TPCH_SF}) written several ways.
-- Loaded once in memory so each COPY only measures encoding + upload.
CREATE TABLE lineitem AS SELECT * FROM read_parquet('gs://${DUCKLESS_BUCKET}/tpch/sf${TPCH_SF}/lineitem/*.parquet');

-- Encoding only: same COPY to local SSD.
COPY lineitem TO '/mnt/disks/scratch/w_local_size' (FORMAT parquet, FILE_SIZE_BYTES '256MB');
COPY lineitem TO '/mnt/disks/scratch/w_local_per_thread' (FORMAT parquet, PER_THREAD_OUTPUT);

-- GCS, current spike setting: one writer rotating files.
COPY lineitem TO 'gs://${DUCKLESS_BUCKET}/writebench/${DUCKLESS_JOB_ID}/size_256mb' (FORMAT parquet, FILE_SIZE_BYTES '256MB');

-- GCS, one file per thread, written concurrently.
COPY lineitem TO 'gs://${DUCKLESS_BUCKET}/writebench/${DUCKLESS_JOB_ID}/per_thread' (FORMAT parquet, PER_THREAD_OUTPUT);

-- GCS, both: concurrent writers that also rotate.
COPY lineitem TO 'gs://${DUCKLESS_BUCKET}/writebench/${DUCKLESS_JOB_ID}/per_thread_256mb' (FORMAT parquet, PER_THREAD_OUTPUT, FILE_SIZE_BYTES '256MB');

-- GCS, Hive partitioning (many files open at once, the typical ETL output).
COPY (SELECT *, year(l_shipdate) AS ship_year FROM lineitem)
TO 'gs://${DUCKLESS_BUCKET}/writebench/${DUCKLESS_JOB_ID}/partitioned' (FORMAT parquet, PARTITION_BY (ship_year));

-- Bytes written per variant, to turn timings into MB/s.
SELECT split_part(filename, '/', -2) AS variant, count(*) AS files, round(sum(size) / 1e9, 2) AS gb
FROM read_blob('gs://${DUCKLESS_BUCKET}/writebench/${DUCKLESS_JOB_ID}/*/*.parquet')
GROUP BY ALL ORDER BY variant;
SELECT 'partitioned' AS variant, count(*) AS files, round(sum(size) / 1e9, 2) AS gb
FROM read_blob('gs://${DUCKLESS_BUCKET}/writebench/${DUCKLESS_JOB_ID}/partitioned/*/*.parquet');
