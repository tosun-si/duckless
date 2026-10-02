"""Work bucket layout: gs://<bucket>/runs/<job_id>/{<source>, metrics.json}."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from google.cloud import storage

RUNS_PREFIX = "runs"
METRICS_FILE = "metrics.json"


def run_object(job_id: str, name: str) -> str:
    return f"{RUNS_PREFIX}/{job_id}/{name}"


def parse_metrics(raw: str) -> Mapping[str, Any] | None:
    """The runner writes metrics with DuckDB's COPY … (FORMAT json): one object per line."""
    first = next((line for line in raw.splitlines() if line.strip()), None)
    return json.loads(first) if first else None


class GcsArtifactStore:
    def __init__(self, client: storage.Client, bucket: str) -> None:
        self._bucket = client.bucket(bucket)

    def upload_source(self, job_id: str, source: Path) -> str:
        blob = self._bucket.blob(run_object(job_id, source.name))
        blob.upload_from_filename(str(source))
        return f"gs://{self._bucket.name}/{blob.name}"

    def runner_env(self, job_id: str) -> Mapping[str, str]:
        return {
            "DUCKLESS_JOB_ID": job_id,
            "DUCKLESS_BUCKET": self._bucket.name,
            "DUCKLESS_METRICS_URI": f"gs://{self._bucket.name}/{run_object(job_id, METRICS_FILE)}",
        }

    def read_metrics(self, job_id: str) -> Mapping[str, Any] | None:
        blob = self._bucket.blob(run_object(job_id, METRICS_FILE))
        return parse_metrics(blob.download_as_text()) if blob.exists() else None
