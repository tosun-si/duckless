"""Organization policies that would make `duckless init` fail, checked before anything is created.

Without this, a blocking policy shows up minutes later as a Terraform error from Infra Manager.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from duckless.core.preflight import PreflightCheck

SA_CREATION = "constraints/iam.disableServiceAccountCreation"
RESOURCE_LOCATIONS = "constraints/gcp.resourceLocations"
NON_CMEK_SERVICES = "constraints/gcp.restrictNonCmekServices"
SERVICE_USAGE = "constraints/gcp.restrictServiceUsage"
CONSTRAINTS = (SA_CREATION, RESOURCE_LOCATIONS, NON_CMEK_SERVICES, SERVICE_USAGE)

# Every API DuckLess uses, by init or by the jobs.
REQUIRED_SERVICES = (
    "artifactregistry.googleapis.com",
    "batch.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "config.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
)
# Services holding DuckLess data or images, which it creates without customer-managed keys.
CMEK_SENSITIVE_SERVICES = ("artifactregistry.googleapis.com", "storage.googleapis.com")

# Value groups of gcp.resourceLocations that cover a region, by region prefix.
_MULTI_REGION_GROUPS = {
    "europe": ("eu", "europe"),
    "us": ("us", "northamerica"),
    "northamerica": ("northamerica",),
    "southamerica": ("southamerica",),
    "asia": ("asia",),
    "australia": ("australia",),
    "me": ("me",),
    "africa": ("africa",),
}


@dataclass(frozen=True, slots=True)
class OrgPolicy:
    """Effective policy of one constraint on the project (boolean or list constraint)."""

    constraint: str
    enforced: bool = False
    all_values: str | None = None  # "ALLOW" / "DENY" when the list policy covers every value
    allowed: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()


def _strip(value: str) -> str:
    """List values may carry an `is:` / `under:` / `in:` prefix; services and plain regions don't need it."""
    return value.removeprefix("is:")


def value_allowed(value: str, policy: OrgPolicy) -> bool:
    if policy.all_values == "DENY" or value in map(_strip, policy.denied):
        return False
    if policy.all_values == "ALLOW" or not policy.allowed:
        return True
    return value in map(_strip, policy.allowed)


def region_groups(region: str) -> tuple[str, ...]:
    """Values of gcp.resourceLocations naming the region directly or through its usual groups."""
    prefix = region.split("-", 1)[0]
    groups = (f"in:{g}-locations" for g in _MULTI_REGION_GROUPS.get(prefix, ()))
    return (region, f"in:{region}-locations", *groups)


def location_check(region: str, policy: OrgPolicy) -> PreflightCheck:
    names = region_groups(region)
    if policy.all_values == "DENY" or any(v in policy.denied for v in names):
        return PreflightCheck("org policy locations", ok=False, detail=f"{region} is denied by {RESOURCE_LOCATIONS}")
    if policy.all_values == "ALLOW" or not policy.allowed or any(v in policy.allowed for v in names):
        return PreflightCheck("org policy locations", ok=True, detail=f"{region} allowed")
    # Allowed values may still cover the region through nested groups: warn, don't block.
    return PreflightCheck(
        "org policy locations",
        ok=True,
        detail=f"{region} is not listed by name in {RESOURCE_LOCATIONS} ({', '.join(policy.allowed)}); "
        "check that one of these groups covers it",
    )


def org_policy_checks(region: str, policies: Mapping[str, OrgPolicy]) -> tuple[PreflightCheck, ...]:
    def get(constraint: str) -> OrgPolicy:
        return policies.get(constraint, OrgPolicy(constraint))

    sa_blocked = get(SA_CREATION).enforced
    blocked_services = [s for s in REQUIRED_SERVICES if not value_allowed(s, get(SERVICE_USAGE))]
    # restrictNonCmekServices is a deny list: a listed service must use customer-managed keys.
    cmek_required = [s for s in CMEK_SENSITIVE_SERVICES if s in map(_strip, get(NON_CMEK_SERVICES).denied)]
    return (
        PreflightCheck(
            "org policy service accounts",
            ok=not sa_blocked,
            detail=f"{SA_CREATION} is enforced: init creates two service accounts"
            if sa_blocked
            else "creation allowed",
        ),
        location_check(region, get(RESOURCE_LOCATIONS)),
        PreflightCheck(
            "org policy services",
            ok=not blocked_services,
            detail=f"{SERVICE_USAGE} blocks {', '.join(blocked_services)}" if blocked_services else "all allowed",
        ),
        PreflightCheck(
            "org policy CMEK",
            ok=not cmek_required,
            detail=f"{NON_CMEK_SERVICES} requires CMEK for {', '.join(cmek_required)}; DuckLess does not set CMEK yet"
            if cmek_required
            else "not required",
        ),
    )
