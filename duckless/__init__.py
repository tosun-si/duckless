"""DuckLess: serverless DuckDB on GCP.

Submit SQL or your own code; DuckLess runs it on a right-sized Compute Engine VM
(Cloud Batch) in your project, with GCS access through ADC, then tears it down.
"""

from duckless.api import Client, JobFailedError, JobTimeoutError
from duckless.core.errors import DucklessError
from duckless.core.job import JobReport, JobState, JobStatus, LogLine

__all__ = [
    "Client",
    "DucklessError",
    "JobFailedError",
    "JobReport",
    "JobState",
    "JobStatus",
    "JobTimeoutError",
    "LogLine",
]
