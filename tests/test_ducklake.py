from dataclasses import replace

from duckless import service
from duckless.adapters.cloud_run import build_job
from duckless.core.infra import (
    CATALOG_INFRA_SA_ROLES,
    INFRA_SA_ROLES,
    InfraStatus,
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
    "duckless", "ACTIVE", {"ducklake_instance": "acme:europe-west1:duckless-catalog", "network": "data-vpc"}
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


class TestInitWithDuckLake:
    def test_given_ducklake_on_a_peered_network_when_init_then_reuses_peering_and_grants_catalog_roles(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(network_peered=True), FakeDeployer()

        # when
        init(bootstrap, deployer, [], replace(REQUEST, ducklake=True))

        # then: an existing peering is never touched (its range list is shared)
        inputs = deployer.applied[0][2]
        assert (inputs["ducklake"], inputs["network"]) == (True, "default")
        assert "psa?:default" in bootstrap.calls
        assert not any(call.startswith("create-psa") for call in bootstrap.calls)
        assert set(CATALOG_INFRA_SA_ROLES) <= set(bootstrap.granted_roles)

    def test_given_ducklake_on_a_network_without_peering_when_init_then_creates_it_before_applying(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(network_peered=False), FakeDeployer()

        # when
        init(bootstrap, deployer, [], replace(REQUEST, ducklake=True, network="data-vpc"))

        # then
        assert "create-psa:data-vpc:duckless-psa" in bootstrap.calls
        assert deployer.applied  # applied after the peering exists

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
        inputs = deployment_inputs(replace(REQUEST, ducklake=True, network="data-vpc"))

        # then
        assert (inputs["ducklake"], inputs["network"]) == (True, "data-vpc")
        assert set(INFRA_SA_ROLES).isdisjoint(CATALOG_INFRA_SA_ROLES)

    def test_given_ducklake_deployment_when_destroying_then_keeps_peering_and_says_how_to_remove_it(self) -> None:
        # given
        steps: list[str] = []

        # when
        service.destroy_infra(
            REQUEST, bootstrap=FakeBootstrap(), deployer=FakeDeployer(current=LAKE_DEPLOYMENT), on_step=steps.append
        )

        # then
        assert any("gcloud services vpc-peerings delete" in step and "data-vpc" in step for step in steps)


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
