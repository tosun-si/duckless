# airflow-provider-duckless

Run [DuckLess](https://github.com/tosun-si/duckless) jobs (serverless DuckDB on Google Cloud) from
Apache Airflow and Cloud Composer.

```python
from duckless_airflow.operators import DuckLessRunOperator

DuckLessRunOperator(task_id="checks", source="jobs/checks.py", machine="n2-highmem-8", env={"DAY": "{{ ds }}"})
```

`DuckLessRunOperator` submits the job, then waits for it in the triggerer (deferrable by default),
so no worker slot is held while it runs. The task fails when the job fails, with the runner's error,
and pushes a summary of the job to XCom.

Airflow 2.10 and later, Airflow 3 included; Python 3.11 to 3.13.

Guide: https://tosun-si.github.io/duckless/guides/airflow/
