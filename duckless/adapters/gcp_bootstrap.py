"""InfraBootstrap with the caller's credentials: plain REST for the few one-off calls, the
storage client for the staging bucket."""

import time
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage

from duckless.core.infra import with_bindings, without_bindings
from duckless.core.org_policy import OrgPolicy

SERVICE_USAGE = "https://serviceusage.googleapis.com/v1"
IAM = "https://iam.googleapis.com/v1"
RESOURCE_MANAGER = "https://cloudresourcemanager.googleapis.com/v3"
SKIPPED_PARTS = frozenset({".terraform", ".terraform.lock.hcl", "__pycache__"})
OPERATION_POLL_SECONDS = 3
# A service account created a moment ago can be unknown to setIamPolicy for up to a minute.
NEW_MEMBER_DELAYS = (2, 4, 8, 15, 30)


def is_unknown_member(status_code: int, body: str) -> bool:
    return status_code == 400 and "does not exist" in body


def module_files(local_dir: Path) -> list[Path]:
    """Files of the Terraform module, without local Terraform state or caches."""
    return sorted(
        p
        for p in local_dir.rglob("*")
        if p.is_file() and not SKIPPED_PARTS.intersection(p.relative_to(local_dir).parts)
    )


def parse_org_policy(constraint: str, body: dict) -> OrgPolicy:
    """Resource Manager v1 getEffectiveOrgPolicy: a booleanPolicy or a listPolicy (or neither: default)."""
    boolean = body.get("booleanPolicy") or {}
    listed = body.get("listPolicy") or {}
    return OrgPolicy(
        constraint=constraint,
        enforced=bool(boolean.get("enforced", False)),
        all_values=listed.get("allValues"),
        allowed=tuple(listed.get("allowedValues", ())),
        denied=tuple(listed.get("deniedValues", ())),
    )


class GcpInfraBootstrap:
    def __init__(self, session: AuthorizedSession, storage_client: storage.Client) -> None:
        self._session = session
        self._storage = storage_client

    def _call(self, method: str, url: str, **kwargs) -> dict:
        response = self._session.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def _wait(self, base: str, operation: dict) -> None:
        while not operation.get("done"):
            time.sleep(OPERATION_POLL_SECONDS)
            operation = self._call("GET", f"{base}/{operation['name']}")
        if "error" in operation:
            raise RuntimeError(f"operation {operation['name']} failed: {operation['error']}")

    def enable_apis(self, project: str, apis: tuple[str, ...]) -> None:
        operation = self._call(
            "POST", f"{SERVICE_USAGE}/projects/{project}/services:batchEnable", json={"serviceIds": list(apis)}
        )
        self._wait(SERVICE_USAGE, operation)

    def effective_org_policy(self, project: str, constraint: str) -> OrgPolicy:
        body = self._call(
            "POST",
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:getEffectiveOrgPolicy",
            json={"constraint": constraint},
        )
        return parse_org_policy(constraint, body)

    def ensure_service_account(self, project: str, account_id: str, display_name: str) -> str:
        email = f"{account_id}@{project}.iam.gserviceaccount.com"
        existing = self._session.get(f"{IAM}/projects/{project}/serviceAccounts/{email}")
        if existing.status_code == 404:
            self._call(
                "POST",
                f"{IAM}/projects/{project}/serviceAccounts",
                json={"accountId": account_id, "serviceAccount": {"displayName": display_name}},
            )
        else:
            existing.raise_for_status()
        return email

    def _update_policy(self, project: str, change) -> bool:
        resource = f"{RESOURCE_MANAGER}/projects/{project}"
        policy = self._call("POST", f"{resource}:getIamPolicy", json={"options": {"requestedPolicyVersion": 3}})
        updated, changed = change(policy)
        if not changed:
            return False
        for delay in (*NEW_MEMBER_DELAYS, None):
            # The etag in `updated` makes a concurrent change fail instead of being overwritten.
            response = self._session.post(f"{resource}:setIamPolicy", json={"policy": updated})
            if delay is None or not is_unknown_member(response.status_code, response.text):
                break
            time.sleep(delay)
        response.raise_for_status()
        return True

    def grant_project_roles(self, project: str, member: str, roles: tuple[str, ...]) -> bool:
        return self._update_policy(project, lambda policy: with_bindings(policy, member, roles))

    def revoke_project_roles(self, project: str, member: str, roles: tuple[str, ...]) -> None:
        self._update_policy(project, lambda policy: without_bindings(policy, member, roles))

    def delete_service_account(self, project: str, email: str) -> None:
        response = self._session.delete(f"{IAM}/projects/{project}/serviceAccounts/{email}")
        if response.status_code != 404:
            response.raise_for_status()

    def delete_cloud_run_jobs(self, project: str, region: str, service_account: str) -> int:
        from google.cloud import run_v2

        client = run_v2.JobsClient()
        jobs = [
            job.name
            for job in client.list_jobs(parent=f"projects/{project}/locations/{region}")
            if job.labels.get("app") == "duckless" and job.template.template.service_account == service_account
        ]
        # Start every deletion, then wait: one at a time takes seconds each.
        for operation in [client.delete_job(name=name) for name in jobs]:
            operation.result()
        return len(jobs)

    def delete_bucket(self, bucket: str) -> None:
        existing = self._storage.lookup_bucket(bucket)
        if existing is not None:
            existing.delete(force=True)

    def ensure_bucket(self, project: str, region: str, bucket: str) -> None:
        if self._storage.lookup_bucket(bucket) is not None:
            return
        new = self._storage.bucket(bucket)
        new.iam_configuration.uniform_bucket_level_access_enabled = True
        new.iam_configuration.public_access_prevention = "enforced"
        new.labels = {"app": "duckless"}
        self._storage.create_bucket(new, project=project, location=region)

    def upload_directory(self, local_dir: Path, bucket: str, prefix: str) -> str:
        target = self._storage.bucket(bucket)
        for path in module_files(local_dir):
            target.blob(f"{prefix}/{path.relative_to(local_dir).as_posix()}").upload_from_filename(str(path))
        return f"gs://{bucket}/{prefix}"
