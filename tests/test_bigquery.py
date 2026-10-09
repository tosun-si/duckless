from dataclasses import replace

import pytest

from duckless.core.infra import (
    BIGQUERY_INFRA_SA_ROLES,
    InfraStatus,
    InvalidInfraRequestError,
    bigquery_dataset_id,
    deployment_inputs,
    infra_sa_roles,
    resolved_request,
)
from tests.test_infra import REQUEST, FakeBootstrap, FakeDeployer, init

BQ_DEPLOYMENT = InfraStatus("duckless", "ACTIVE", {"bigquery_datasets": ["sales"], "bigquery_jobs": True})


class TestDatasetNames:
    @pytest.mark.parametrize("given", ["sales", "acme-data:sales", "acme-data.sales"])
    def test_given_dataset_of_the_project_when_normalizing_then_its_id(self, given: str) -> None:
        # when / then
        assert bigquery_dataset_id("acme-data", given) == "sales"

    def test_given_dataset_of_another_project_when_normalizing_then_refused_with_what_to_do(self) -> None:
        # when / then
        with pytest.raises(InvalidInfraRequestError, match=r"roles/bigquery\.dataViewer"):
            bigquery_dataset_id("acme-data", "other-project:ref")

    @pytest.mark.parametrize("given", ["sales-eu", "sa les", ""])
    def test_given_invalid_name_when_normalizing_then_refused(self, given: str) -> None:
        # when / then
        with pytest.raises(InvalidInfraRequestError):
            bigquery_dataset_id("acme-data", given)

    def test_given_request_with_foreign_dataset_when_building_then_refused_before_anything_runs(self) -> None:
        # when / then
        with pytest.raises(InvalidInfraRequestError):
            replace(REQUEST, bigquery_datasets=("other:ref",))


class TestStickyOptions:
    def test_given_deployment_with_bigquery_when_init_without_options_then_kept(self) -> None:
        # when
        request = resolved_request(REQUEST, BQ_DEPLOYMENT)

        # then
        assert (request.bigquery_datasets, request.bigquery_jobs) == (("sales",), True)

    def test_given_explicit_options_when_resolving_then_they_win(self) -> None:
        # when
        request = resolved_request(replace(REQUEST, bigquery_datasets=("finance",), bigquery_jobs=False), BQ_DEPLOYMENT)

        # then
        assert (request.bigquery_datasets, request.bigquery_jobs) == (("finance",), False)


class TestInputsAndRoles:
    def test_given_datasets_when_building_inputs_then_dataset_ids(self) -> None:
        # when
        inputs = deployment_inputs(replace(REQUEST, bigquery_datasets=("acme-data:sales", "ref"), bigquery_jobs=True))

        # then
        assert (inputs["bigquery_datasets"], inputs["bigquery_jobs"]) == (["sales", "ref"], True)

    def test_given_datasets_when_init_then_infra_account_may_grant_on_them(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(), FakeDeployer()

        # when
        init(bootstrap, deployer, [], replace(REQUEST, bigquery_datasets=("sales",)))

        # then
        assert set(BIGQUERY_INFRA_SA_ROLES) <= set(bootstrap.granted_roles)

    def test_given_no_bigquery_when_init_then_no_bigquery_role(self) -> None:
        # given
        bootstrap = FakeBootstrap()

        # when
        init(bootstrap, FakeDeployer(), [])

        # then
        assert set(BIGQUERY_INFRA_SA_ROLES).isdisjoint(bootstrap.granted_roles)
        assert set(BIGQUERY_INFRA_SA_ROLES) <= set(infra_sa_roles(ducklake=False, bigquery=True))
