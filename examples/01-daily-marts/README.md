# 01 · Daily marts

A daily transformation in plain SQL: four raw tables in, two marts out.

```bash
duckless run examples/01-daily-marts/marts.sql -m n2-standard-8
duckless result <job-id>
```

| Mart | Written as | Why |
| --- | --- | --- |
| `marts/revenue_by_nation_month/` | partitioned by `year` (`year=1997/…`) | readers filtering on a year open only its files |
| `marts/customer_value/` | one file per thread, 256 MB max each | the fastest way to write a large table to GCS |

The last statement reads the first mart back and is what `duckless result` shows: the five
nations with the most revenue in 1997.

`n2-standard-8` fits Cloud Run Jobs (8 vCPU, 32 GiB), so the job starts in about 30 seconds.
On `SCALE=100` data, give it a bigger machine; it then runs on Cloud Batch:

```bash
duckless run examples/01-daily-marts/marts.sql -m n2-highmem-32 --spot
```

To run it every day, schedule the same command (Cloud Scheduler, a CI job, Airflow): every
write uses `OVERWRITE`, so a re-run replaces the marts instead of duplicating them.
