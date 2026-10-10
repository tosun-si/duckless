"""Outbound ports: what the service needs from the outside world. Adapters live in duckless.adapters."""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from duckless.core.infra import InfraStatus
from duckless.core.job import JobSpec, JobStatus, LogLine
from duckless.core.org_policy import OrgPolicy
from duckless.core.quota import Quota


class Executor(Protocol):
    """Runs a job on some compute: Cloud Batch today, Cloud Run Jobs / GKE later."""

    def submit(self, spec: JobSpec) -> JobStatus: ...

    def get(self, job_id: str) -> JobStatus:
        """Raises JobNotFoundError."""
        ...

    def cancel(self, job_id: str) -> None:
        """Raises JobNotFoundError."""
        ...


class ArtifactStore(Protocol):
    """Per-job files in the work bucket: the submitted source, the runner's metrics."""

    def upload_source(self, job_id: str, source: Path) -> str:
        """Returns the URI the runner reads the source from."""
        ...

    def runner_env(self, job_id: str) -> Mapping[str, str]:
        """Env the runner needs to find the work bucket and write its metrics."""
        ...

    def read_metrics(self, job_id: str) -> Mapping[str, Any] | None:
        """None until the runner has written them."""
        ...


class LogReader(Protocol):
    def read(self, status: JobStatus, since: datetime | None = None, limit: int = 200) -> tuple[LogLine, ...]:
        """Runner log lines of a job, oldest first, wherever it ran (status.executor)."""
        ...


class QuotaReader(Protocol):
    def regional_quotas(self, region: str) -> Mapping[str, Quota]: ...


class InfraBootstrap(Protocol):
    """What `init` sets up with the caller's credentials before Infra Manager can run."""

    def enable_apis(self, project: str, apis: tuple[str, ...]) -> None: ...

    def effective_org_policy(self, project: str, constraint: str) -> OrgPolicy:
        """Policy that applies to the project, inherited from folders and organization."""
        ...

    def ensure_service_account(self, project: str, account_id: str, display_name: str) -> str:
        """Returns the service account email."""
        ...

    def grant_project_roles(
        self, project: str, member: str, roles: tuple[str, ...], condition: Mapping[str, str] | None = None
    ) -> bool:
        """True when the project IAM policy changed (new grants take a while to propagate)."""
        ...

    def ensure_bucket(self, project: str, region: str, bucket: str) -> None: ...

    def upload_directory(self, local_dir: Path, bucket: str, prefix: str) -> str:
        """Returns the gs:// URI of the uploaded directory."""
        ...

    def revoke_project_roles(
        self, project: str, member: str, roles: tuple[str, ...], condition: Mapping[str, str] | None = None
    ) -> None: ...

    def delete_service_account(self, project: str, email: str) -> None:
        """No-op when it does not exist."""
        ...

    def has_private_service_access(self, project: str, network: str) -> bool:
        """True when the network is already peered with Google services (servicenetworking)."""
        ...

    def create_private_service_access(self, project: str, network: str, range_name: str) -> None:
        """Reserves a /20 range (labelled app=duckless) and peers the network with Google services."""
        ...

    def delete_cloud_run_jobs(self, project: str, region: str, service_account: str) -> int:
        """DuckLess Cloud Run jobs (one per run) running as this service account; returns how many."""
        ...

    def delete_bucket(self, bucket: str) -> None:
        """Deletes its objects too; no-op when it does not exist."""
        ...

    def service_account_exists(self, project: str, email: str) -> bool: ...

    def bucket_exists(self, bucket: str) -> bool: ...

    def bucket_has_objects(self, bucket: str) -> bool:
        """False when the bucket is empty or does not exist."""
        ...


class InfraDeployer(Protocol):
    """Infrastructure Manager: applies a Terraform module and keeps its state."""

    def apply(
        self,
        project: str,
        region: str,
        deployment_id: str,
        source_uri: str,
        inputs: Mapping[str, Any],
        service_account: str,
    ) -> InfraStatus: ...

    def get(self, project: str, region: str, deployment_id: str) -> InfraStatus | None: ...

    def destroy(self, project: str, region: str, deployment_id: str) -> InfraStatus: ...
