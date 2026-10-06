import pytest

from duckless.adapters.gcp_bootstrap import parse_org_policy
from duckless.core.org_policy import (
    NON_CMEK_SERVICES,
    RESOURCE_LOCATIONS,
    SA_CREATION,
    SERVICE_USAGE,
    OrgPolicy,
    location_check,
    org_policy_checks,
    value_allowed,
)


def failing(checks) -> list[str]:
    return [c.name for c in checks if not c.ok]


class TestOrgPolicyChecks:
    def test_given_no_policy_when_checking_then_everything_passes(self) -> None:
        # when / then
        assert failing(org_policy_checks("europe-west1", {})) == []

    def test_given_sa_creation_disabled_when_checking_then_blocks(self) -> None:
        # when
        checks = org_policy_checks("europe-west1", {SA_CREATION: OrgPolicy(SA_CREATION, enforced=True)})

        # then
        assert failing(checks) == ["org policy service accounts"]

    def test_given_batch_not_in_allowed_services_when_checking_then_blocks_naming_it(self) -> None:
        # given
        policy = OrgPolicy(SERVICE_USAGE, allowed=("storage.googleapis.com", "compute.googleapis.com"))

        # when
        (services,) = [c for c in org_policy_checks("europe-west1", {SERVICE_USAGE: policy}) if not c.ok]

        # then
        assert "batch.googleapis.com" in services.detail

    def test_given_cmek_required_on_storage_when_checking_then_blocks(self) -> None:
        # given
        policy = OrgPolicy(NON_CMEK_SERVICES, denied=("is:storage.googleapis.com",))

        # when / then
        assert failing(org_policy_checks("europe-west1", {NON_CMEK_SERVICES: policy})) == ["org policy CMEK"]


class TestLocationCheck:
    @pytest.mark.parametrize(
        "allowed", [("in:eu-locations",), ("in:europe-west1-locations",), ("europe-west1",), ("in:europe-locations",)]
    )
    def test_given_region_covered_by_name_or_usual_group_when_checking_then_ok(self, allowed: tuple[str, ...]) -> None:
        # when
        check = location_check("europe-west1", OrgPolicy(RESOURCE_LOCATIONS, allowed=allowed))

        # then
        assert check.ok
        assert check.detail == "europe-west1 allowed"

    def test_given_region_denied_when_checking_then_blocks(self) -> None:
        # when / then
        assert not location_check("us-central1", OrgPolicy(RESOURCE_LOCATIONS, denied=("in:us-locations",))).ok

    def test_given_unrelated_allowed_groups_when_checking_then_warns_without_blocking(self) -> None:
        # when
        check = location_check("europe-west1", OrgPolicy(RESOURCE_LOCATIONS, allowed=("in:my-org-locations",)))

        # then
        assert check.ok
        assert "not listed by name" in check.detail


class TestValueAllowed:
    @pytest.mark.parametrize(
        ("policy", "allowed"),
        [
            (OrgPolicy(SERVICE_USAGE), True),
            (OrgPolicy(SERVICE_USAGE, all_values="DENY"), False),
            (OrgPolicy(SERVICE_USAGE, denied=("batch.googleapis.com",)), False),
            (OrgPolicy(SERVICE_USAGE, allowed=("is:batch.googleapis.com",)), True),
        ],
    )
    def test_given_policy_when_checking_batch_then_matches_rules(self, policy: OrgPolicy, allowed: bool) -> None:
        # when / then
        assert value_allowed("batch.googleapis.com", policy) is allowed


class TestParseOrgPolicy:
    def test_given_boolean_policy_when_parsing_then_enforced(self) -> None:
        # when / then
        assert parse_org_policy(SA_CREATION, {"booleanPolicy": {"enforced": True}}).enforced

    def test_given_list_policy_when_parsing_then_values_kept(self) -> None:
        # when
        policy = parse_org_policy(RESOURCE_LOCATIONS, {"listPolicy": {"allowedValues": ["in:eu-locations"]}})

        # then
        assert policy.allowed == ("in:eu-locations",)
        assert policy.all_values is None

    def test_given_default_policy_when_parsing_then_nothing_restricted(self) -> None:
        # when / then
        assert parse_org_policy(SERVICE_USAGE, {"constraint": SERVICE_USAGE}) == OrgPolicy(SERVICE_USAGE)
