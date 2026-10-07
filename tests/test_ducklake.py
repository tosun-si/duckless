from dataclasses import replace

import pytest

from duckless.adapters.cloud_run import build_job
from duckless.core.infra import (
    CATALOG_INFRA_SA_ROLES,
    INFRA_SA_ROLES,
    InfraStatus,
    creates_private_service_access,
    deployment_inputs,
    resolved_request,
)
from duckless.core.lake import LakeConfig, lake_env
from duckless.settings import Settings
from tests.test_infra import REQUEST, FakeBootstrap, FakeDeployer, init
from tests.test_routing import cloud_run_spec

ENV = {"DUCKLESS_PROJECT": "acme", "DUCKLESS_BUCKET": "work", "DUCKLESS_SA": "runner@acme.iam.gserviceaccount.com"}
LAKE_ENV = {
    **ENV,
    "DUCKLESS_IMAGE": "img",
    "DUCKLESS_DUCKLAKE_INSTANCE": "acme:europe-west1:duckless-catalog",
    "DUCKLESS_DUCKLAKE_DATA_PATH": "gcss://work/lake/",
}
LAKE_DEPLOYMENT = InfraStatus(
    "duckless",
    "ACTIVE",
    {
        "ducklake_instance": "acme:europe-west1:duckless-catalog",
        "network": "data-vpc",
        "private_service_access_created": True,
    },
)


class TestResolvedRequest:
    def test_given_new_deployment_and_no_option_when_resolving_then_no_ducklake_on_default_network(self) -> None:
        # when
        request = resolved_request(REQUEST, None)

        # then
        assert (request.ducklake, request.network) == (False, "default")

    def test_given_ducklake_deployment_and_no_option_when_resolving_then_keeps_catalog_and_network(self) -> None:
        # when: an upgrade, or the re-apply of `destroy --force`
        request = resolved_request(REQUEST, LAKE_DEPLOYMENT)

        # then
        assert (request.ducklake, request.network) == (True, "data-vpc")

    def test_given_explicit_no_ducklake_when_resolving_then_the_option_wins(self) -> None:
        # when
        request = resolved_request(replace(REQUEST, ducklake=False), LAKE_DEPLOYMENT)

        # then
        assert request.ducklake is False


class TestPrivateServiceAccess:
    @pytest.mark.parametrize(
        ("current", "peered", "creates"),
        [
            (None, False, True),  # new network: the module peers it
            (None, True, False),  # already peered (e.g. other Cloud SQL): never rewrite its ranges
            (LAKE_DEPLOYMENT, True, True),  # peered by this module earlier: keep managing it
        ],
    )
    def test_given_network_state_when_deciding_then_module_only_owns_the_peering_it_created(
        self, current: InfraStatus | None, peered: bool, creates: bool
    ) -> None:
        # when / then
        assert creates_private_service_access(current, peered) is creates


class TestInitWithDuckLake:
    def test_given_ducklake_on_a_peered_network_when_init_then_reuses_peering_and_grants_catalog_roles(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(network_peered=True), FakeDeployer()

        # when
        init(bootstrap, deployer, [], replace(REQUEST, ducklake=True))

        # then
        inputs = deployer.applied[0][2]
        assert (inputs["ducklake"], inputs["network"], inputs["create_private_service_access"]) == (
            True,
            "default",
            False,
        )
        assert "psa?:default" in bootstrap.calls
        assert set(CATALOG_INFRA_SA_ROLES) <= set(bootstrap.granted_roles)

    def test_given_no_ducklake_when_init_then_network_not_inspected_and_no_catalog_roles(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(), FakeDeployer()

        # when
        init(bootstrap, deployer, [])

        # then
        assert not any(call.startswith("psa?") for call in bootstrap.calls)
        assert deployer.applied[0][2]["ducklake"] is False
        assert set(bootstrap.granted_roles) == set(INFRA_SA_ROLES)

    def test_given_inputs_when_building_then_module_variables(self) -> None:
        # when
        inputs = deployment_inputs(replace(REQUEST, ducklake=True, network="data-vpc"), True)

        # then
        assert {k: inputs[k] for k in ("ducklake", "network", "create_private_service_access")} == {
            "ducklake": True,
            "network": "data-vpc",
            "create_private_service_access": True,
        }
        assert set(INFRA_SA_ROLES).isdisjoint(CATALOG_INFRA_SA_ROLES)


class TestLakeEnv:
    def test_given_lake_settings_when_building_env_then_runner_attaches_catalog_as_the_runner_sa(self) -> None:
        # given
        settings = Settings.from_env(LAKE_ENV)

        # when
        env = lake_env(settings.lake)

        # then
        assert env == {
            "DUCKLESS_DUCKLAKE_INSTANCE": "acme:europe-west1:duckless-catalog",
            "DUCKLESS_DUCKLAKE_DATA_PATH": "gcss://work/lake/",
            "DUCKLESS_DUCKLAKE_USER": "runner@acme.iam.gserviceaccount.com",
        }

    def test_given_no_catalog_when_building_env_then_empty(self) -> None:
        # when / then
        assert Settings.from_env({**ENV, "DUCKLESS_IMAGE": "img"}).lake is None
        assert lake_env(None) == {}
        assert lake_env(LakeConfig("i", "d", "sa"))["DUCKLESS_DUCKLAKE_USER"] == "sa"


class TestCloudRunVpcAccess:
    def test_given_ducklake_when_building_cloud_run_job_then_direct_vpc_egress_to_private_ranges(self) -> None:
        # when
        task = build_job(cloud_run_spec(), Settings.from_env(LAKE_ENV)).template.template

        # then
        interface = task.vpc_access.network_interfaces[0]
        assert interface.network == "projects/acme/global/networks/default"
        assert interface.subnetwork == "projects/acme/regions/europe-west1/subnetworks/default"
        assert task.vpc_access.egress.name == "PRIVATE_RANGES_ONLY"

    def test_given_no_ducklake_when_building_cloud_run_job_then_no_vpc(self) -> None:
        # when
        task = build_job(cloud_run_spec(), Settings.from_env({**ENV, "DUCKLESS_IMAGE": "img"})).template.template

        # then
        assert not task.vpc_access.network_interfaces
