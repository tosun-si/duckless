"""Apache Airflow provider for DuckLess: run DuckDB jobs on Google Cloud from a DAG.

from duckless_airflow.operators import DuckLessRunOperator

DuckLessRunOperator(task_id="quality_checks", source="jobs/checks.py", machine="n2-highmem-8")
"""

from importlib import metadata


def get_provider_info() -> dict:
    """What Airflow shows about the provider (Admin > Providers)."""
    return {
        "package-name": "airflow-provider-duckless",
        "name": "DuckLess",
        "description": "Run DuckDB jobs on Google Cloud (Cloud Batch, Cloud Run Jobs) with DuckLess.",
        "versions": [metadata.version("airflow-provider-duckless")],
    }
