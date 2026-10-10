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
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.admin",
)

# Project roles the module grants the runner account: the only roles the Infrastructure Manager
# account may grant, through a condition on its project IAM admin role.
RUNNER_PROJECT_ROLES = (
    "roles/batch.agentReporter",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/bigquery.readSessionUser",
    "roles/bigquery.jobUser",
)
IAM_ADMIN_ROLE = "roles/resourcemanager.projectIamAdmin"
RUNNER_GRANTS_ONLY = {
    "title": "duckless-runner-roles-only",
    "description": "Grant or revoke only the project roles of the DuckLess runner account",
    "expression": "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(["
    + ", ".join(f"'{r}'" for r in RUNNER_PROJECT_ROLES)
    + "])",
}

# Only with DuckLake: the catalog instance (the network peering is set up by `init` itself).
CATALOG_INFRA_SA_ROLES = ("roles/cloudsql.admin",)
# Only with BigQuery datasets: the module grants the runner read access on each of them.
BIGQUERY_INFRA_SA_ROLES = ("roles/bigquery.dataOwner",)
# APIs `init` needs to set up the network's private services access before applying.
CATALOG_BOOTSTRAP_APIS = ("compute.googleapis.com", "servicenetworking.googleapis.com")

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
    bigquery_datasets: tuple[str, ...] | None = None  # None: keep the deployment's
    bigquery_jobs: bool | None = None  # None: keep the deployment's (off for a new one)

    def __post_init__(self) -> None:
        if not self.project or not self.region:
            raise InvalidInfraRequestError("project and region are required")
        if not _NAME_RE.match(self.name):
            # Prefixes a service account id (6-30 chars) and bucket names.
            raise InvalidInfraRequestError(f"name '{self.name}' must match {_NAME_RE.pattern}")
        for dataset in self.bigquery_datasets or ():
            bigquery_dataset_id(self.project, dataset)


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


def infra_sa_roles(ducklake: bool, bigquery: bool = False) -> tuple[str, ...]:
    return INFRA_SA_ROLES + (CATALOG_INFRA_SA_ROLES if ducklake else ()) + (BIGQUERY_INFRA_SA_ROLES if bigquery else ())


def bigquery_dataset_id(project: str, dataset: str) -> str:
    """`dataset`, `project:dataset` or `project.dataset` -> the dataset id, in the installation's project.

    Datasets of other projects are granted by their owners: Infrastructure Manager only acts in
    the installation's project.
    """
    owner, _, name = dataset.replace(":", ".", 1).rpartition(".")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", name):
        raise InvalidInfraRequestError(f"'{dataset}' is not a BigQuery dataset name")
    if owner and owner != project:
        raise InvalidInfraRequestError(
            f"dataset '{dataset}' is in another project: ask its owners to grant the runner "
            "roles/bigquery.dataViewer on it (init grants datasets of its own project only)"
        )
    return name


def resolved_request(request: InfraRequest, current: InfraStatus | None) -> InfraRequest:
    """Options left unset keep the deployment's values: an upgrade or `destroy --force` without
    --ducklake must not plan the catalog's deletion."""
    outputs = current.outputs if current else {}
    return replace(
        request,
        ducklake=request.ducklake if request.ducklake is not None else bool(outputs.get("ducklake_instance")),
        network=request.network or outputs.get("network") or "default",
        bigquery_datasets=(
            request.bigquery_datasets
            if request.bigquery_datasets is not None
            else tuple(outputs.get("bigquery_datasets") or ())
        ),
        bigquery_jobs=(
            request.bigquery_jobs if request.bigquery_jobs is not None else bool(outputs.get("bigquery_jobs"))
        ),
    )


def psa_range_name(request: InfraRequest) -> str:
    return f"{request.name}-psa"


def psa_cleanup_command(project: str, network: str) -> str:
    return (
        f"gcloud services vpc-peerings delete --project {project} --network {network} "
        "--service servicenetworking.googleapis.com"
    )


def deployment_inputs(request: InfraRequest) -> dict[str, Any]:
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
        "bigquery_datasets": [bigquery_dataset_id(request.project, d) for d in request.bigquery_datasets or ()],
        "bigquery_jobs": bool(request.bigquery_jobs),
    }


def _matches(binding: Mapping[str, Any], role: str, condition: Mapping[str, str] | None) -> bool:
    """Same role, and the same condition (none, or the one with this title)."""
    if binding["role"] != role:
        return False
    if condition is None:
        return "condition" not in binding
    return binding.get("condition", {}).get("title") == condition["title"]


def _version(policy: Mapping[str, Any], condition: Mapping[str, str] | None) -> int:
    # Conditional bindings need policy version 3.
    return max(int(policy.get("version", 1)), 3 if condition else 1)


def with_bindings(
    policy: Mapping[str, Any], member: str, roles: tuple[str, ...], condition: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], bool]:
    """IAM policy with `member` added to each role, under `condition` if given; True when it changed."""
    bindings = [dict(b) for b in policy.get("bindings", [])]
    missing = [r for r in roles if not any(_matches(b, r, condition) and member in b["members"] for b in bindings)]
    if not missing:
        return dict(policy), False
    existing = {r for r in missing if any(_matches(b, r, condition) for b in bindings)}
    updated = [
        {**b, "members": [*b["members"], member]} if any(_matches(b, r, condition) for r in existing) else b
        for b in bindings
    ]
    new = [
        {"role": r, "members": [member], **({"condition": dict(condition)} if condition else {})}
        for r in missing
        if r not in existing
    ]
    return {**policy, "bindings": updated + new, "version": _version(policy, condition)}, True


def without_bindings(
    policy: Mapping[str, Any], member: str, roles: tuple[str, ...], condition: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], bool]:
    """IAM policy with `member` removed from each role (under `condition` if given); True when it changed."""
    bindings = [dict(b) for b in policy.get("bindings", [])]
    targeted = [b for b in bindings if any(_matches(b, r, condition) for r in roles) and member in b["members"]]
    if not targeted:
        return dict(policy), False
    updated = [{**b, "members": [m for m in b["members"] if m != member]} if b in targeted else b for b in bindings]
    return {**policy, "bindings": [b for b in updated if b["members"]], "version": _version(policy, condition)}, True


def envrc_lines(status: InfraStatus) -> list[str]:
    return [line.strip() for line in str(status.outputs.get("envrc", "")).splitlines() if line.strip()]


@dataclass(frozen=True, slots=True)
class DestroyPlan:
    """What `duckless destroy` would delete, read before anything is touched."""

    name: str
    deployment_exists: bool
    work_bucket: str | None = None
    work_bucket_has_objects: bool = False
    catalog_instance: str | None = None
    # What `init` creates outside Terraform, left behind when a destroy stopped after the deployment.
    leftovers: bool = False


def destroy_plan(
    name: str, current: InfraStatus | None, work_bucket_has_objects: bool, leftovers: bool = False
) -> DestroyPlan:
    outputs = current.outputs if current else {}
    return DestroyPlan(
        name=name,
        deployment_exists=current is not None,
        work_bucket=outputs.get("work_bucket") or None,
        work_bucket_has_objects=work_bucket_has_objects,
        catalog_instance=outputs.get("ducklake_instance") or None,
        leftovers=leftovers,
    )


def destroy_blockers(plan: DestroyPlan, force: bool) -> tuple[str, ...]:
    """Why destroy refuses to start; empty when it may go on."""
    if not plan.deployment_exists and not plan.leftovers:
        return (f"no DuckLess installation '{plan.name}' here (check --name, --project, --region)",)
    if force:
        return ()
    return tuple(
        reason
        for reason in (
            f"the work bucket {plan.work_bucket} still holds objects" if plan.work_bucket_has_objects else None,
            f"the installation has a DuckLake catalog ({plan.catalog_instance}): deleting it deletes the lake's tables"
            if plan.catalog_instance
            else None,
        )
        if reason
    )


def destroy_summary(plan: DestroyPlan) -> list[str]:
    if not plan.deployment_exists:
        return [
            f"duckless destroy '{plan.name}': the deployment is already gone; it finishes an interrupted destroy:",
            "  - the Infrastructure Manager account (and any role it still holds) and its staging bucket",
        ]
    lines = [f"duckless destroy '{plan.name}' deletes:"]
    if plan.work_bucket:
        objects = " and everything in it" if plan.work_bucket_has_objects else " (empty)"
        lines.append(f"  - the work bucket {plan.work_bucket}{objects}")
    if plan.catalog_instance:
        lines.append(
            f"  - the DuckLake catalog {plan.catalog_instance}: the lake's tables (the Parquet files stay in the "
            "bucket only if it is kept; a final backup of the catalog is kept 30 days by default)"
        )
    lines += [
        "  - the runner service account, the image repository, the Cloud Run jobs of past runs",
        "  - the Infrastructure Manager account and its staging bucket",
        "  kept: your data buckets and BigQuery datasets, enabled APIs, the network peering",
    ]
    return lines


def needs_typed_name(plan: DestroyPlan) -> bool:
    """Deleting a lake takes typing the installation's name: --yes alone is not enough."""
    return plan.catalog_instance is not None
