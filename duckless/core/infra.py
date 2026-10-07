"""`duckless init` / `destroy`: the DuckLess infra of a project, applied by Infrastructure Manager.

Infra Manager runs the Terraform module shipped in this package with a dedicated service
account, from a copy uploaded to a staging bucket: the module version is always the CLI's.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from duckless.core.errors import DucklessError

# APIs `init` itself needs before Infra Manager can run; the module enables the rest.
BOOTSTRAP_APIS = (
    "cloudresourcemanager.googleapis.com",
    "config.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
)

# What the Infra Manager service account needs to apply the module, and nothing more.
INFRA_SA_ROLES = (
    "roles/artifactregistry.admin",
    "roles/config.agent",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.admin",
)

# Only with DuckLake: the catalog instance, its private IP range and the network peering.
CATALOG_INFRA_SA_ROLES = (
    "roles/cloudsql.admin",
    "roles/compute.networkAdmin",
    "roles/servicenetworking.networksAdmin",
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,15}$")


class InvalidInfraRequestError(DucklessError):
    pass


@dataclass(frozen=True, slots=True)
class InfraRequest:
    project: str
    region: str
    name: str = "duckless"
    data_buckets: tuple[str, ...] = ()
    runner_image_tag: str = "edge"
    force_destroy: bool = False
    ducklake: bool | None = None  # None: keep what the deployment has (off for a new one)
    network: str | None = None  # None: keep what the deployment has ("default" for a new one)

    def __post_init__(self) -> None:
        if not self.project or not self.region:
            raise InvalidInfraRequestError("project and region are required")
        if not _NAME_RE.match(self.name):
            # Prefixes a service account id (6-30 chars) and bucket names.
            raise InvalidInfraRequestError(f"name '{self.name}' must match {_NAME_RE.pattern}")


@dataclass(frozen=True, slots=True)
class InfraStatus:
    deployment: str
    state: str
    outputs: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def default_runner_tag(cli_version: str) -> str:
    """A released CLI runs the runner image of its own version; a dev build runs the edge image."""
    return "edge" if any(marker in cli_version for marker in ("dev", "+")) else cli_version


def deployment_id(request: InfraRequest) -> str:
    return request.name


def infra_service_account_id(request: InfraRequest) -> str:
    return f"{request.name}-infra"


def runner_service_account(request: InfraRequest) -> str:
    """Created by the module (`${name}-runner`): every job of this installation runs as it."""
    return f"{request.name}-runner@{request.project}.iam.gserviceaccount.com"


def staging_bucket(request: InfraRequest) -> str:
    return f"{request.project}-{request.name}-infra"


def module_prefix(version: str) -> str:
    return f"module/{version}"


def infra_sa_roles(ducklake: bool) -> tuple[str, ...]:
    return INFRA_SA_ROLES + (CATALOG_INFRA_SA_ROLES if ducklake else ())


def resolved_request(request: InfraRequest, current: InfraStatus | None) -> InfraRequest:
    """Options left unset keep the deployment's values: an upgrade or `destroy --force` without
    --ducklake must not plan the catalog's deletion."""
    outputs = current.outputs if current else {}
    return replace(
        request,
        ducklake=request.ducklake if request.ducklake is not None else bool(outputs.get("ducklake_instance")),
        network=request.network or outputs.get("network") or "default",
    )


def creates_private_service_access(current: InfraStatus | None, network_peered: bool) -> bool:
    """The module creates the peering only on a network without one, and keeps managing the
    one it created (it is peered by then, but dropping it from the state would delete it)."""
    created_before = bool(current and current.outputs.get("private_service_access_created"))
    return created_before or not network_peered


def deployment_inputs(request: InfraRequest, create_private_service_access: bool = False) -> dict[str, Any]:
    """Terraform input values of the module (see duckless/terraform/variables.tf)."""
    return {
        "project_id": request.project,
        "region": request.region,
        "name": request.name,
        "data_buckets": list(request.data_buckets),
        "runner_image_tag": request.runner_image_tag,
        "force_destroy": request.force_destroy,
        "ducklake": bool(request.ducklake),
        "network": request.network or "default",
        "create_private_service_access": create_private_service_access,
    }


def with_bindings(policy: Mapping[str, Any], member: str, roles: tuple[str, ...]) -> tuple[dict[str, Any], bool]:
    """IAM policy with `member` added to each role (unconditional bindings); True when it changed."""
    bindings = [dict(b) for b in policy.get("bindings", [])]
    unconditional = {b["role"]: b for b in bindings if "condition" not in b}
    missing = [r for r in roles if member not in unconditional.get(r, {}).get("members", [])]
    if not missing:
        return dict(policy), False
    updated = [
        {**b, "members": [*b["members"], member]} if "condition" not in b and b["role"] in missing else b
        for b in bindings
    ]
    new = [{"role": r, "members": [member]} for r in missing if r not in unconditional]
    return {**policy, "bindings": updated + new, "version": max(int(policy.get("version", 1)), 1)}, True


def without_bindings(policy: Mapping[str, Any], member: str, roles: tuple[str, ...]) -> tuple[dict[str, Any], bool]:
    """IAM policy with `member` removed from each role (unconditional bindings); True when it changed."""
    bindings = [dict(b) for b in policy.get("bindings", [])]
    targeted = [b for b in bindings if "condition" not in b and b["role"] in roles and member in b["members"]]
    if not targeted:
        return dict(policy), False
    updated = [{**b, "members": [m for m in b["members"] if m != member]} if b in targeted else b for b in bindings]
    return {**policy, "bindings": [b for b in updated if b["members"]]}, True


def envrc_lines(status: InfraStatus) -> list[str]:
    return [line.strip() for line in str(status.outputs.get("envrc", "")).splitlines() if line.strip()]
