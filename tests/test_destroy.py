from dataclasses import dataclass, field

import pytest
from typer.testing import CliRunner

from duckless import cli
from duckless.core.infra import (
    DestroyPlan,
    InfraStatus,
    destroy_blockers,
    destroy_plan,
    destroy_summary,
    needs_typed_name,
)
from duckless.wiring import InfraServices

LAKE = InfraStatus(
    "duckless",
    "ACTIVE",
    {"work_bucket": "acme-duckless-work", "ducklake_instance": "acme:europe-west1:duckless-catalog"},
)
PLAIN = InfraStatus("duckless", "ACTIVE", {"work_bucket": "acme-duckless-work"})


class TestDestroyDecision:
    def test_given_no_installation_when_planning_then_refused_whatever_the_flags(self) -> None:
        # when / then
        assert destroy_blockers(destroy_plan("duckless", None, False), force=True)

    def test_given_catalog_without_force_when_planning_then_refused_before_anything(self) -> None:
        # when
        blockers = destroy_blockers(destroy_plan("duckless", LAKE, False), force=False)

        # then
        assert len(blockers) == 1 and "DuckLake catalog" in blockers[0]

    def test_given_non_empty_bucket_without_force_when_planning_then_refused(self) -> None:
        # when / then
        assert "still holds objects" in destroy_blockers(destroy_plan("duckless", PLAIN, True), force=False)[0]

    def test_given_empty_install_when_planning_then_allowed_without_force_or_typed_name(self) -> None:
        # given
        plan = destroy_plan("duckless", PLAIN, False)

        # when / then
        assert destroy_blockers(plan, force=False) == () and not needs_typed_name(plan)

    def test_given_catalog_with_force_when_planning_then_allowed_but_name_must_be_typed(self) -> None:
        # given
        plan = destroy_plan("duckless", LAKE, False)

        # when / then
        assert destroy_blockers(plan, force=True) == () and needs_typed_name(plan)


@dataclass
class Calls:
    destroyed: list[str] = field(default_factory=list)


def services(plan: DestroyPlan, calls: Calls) -> InfraServices:
    def destroy(request, on_step) -> InfraStatus:
        calls.destroyed.append(request.name)
        return InfraStatus(request.name, "DELETED")

    return InfraServices(init=lambda r, s: InfraStatus("d", "ACTIVE"), destroy=destroy, plan_destroy=lambda r: plan)


class TestDestroyCommand:
    @pytest.fixture
    def run(self, monkeypatch: pytest.MonkeyPatch):
        def invoke(plan: DestroyPlan, *args: str, stdin: str = ""):
            calls = Calls()
            monkeypatch.setattr(cli, "_infra", lambda: services(plan, calls))
            result = CliRunner().invoke(cli.app, ["destroy", "--project", "acme", *args], input=stdin)
            return result, calls

        return invoke

    def test_given_catalog_and_yes_only_when_destroying_then_nothing_deleted(self, run) -> None:
        # when
        result, calls = run(destroy_plan("duckless", LAKE, False), "--yes")

        # then
        assert result.exit_code == 1 and calls.destroyed == []

    def test_given_catalog_force_and_wrong_name_when_destroying_then_nothing_deleted(self, run) -> None:
        # when
        result, calls = run(destroy_plan("duckless", LAKE, False), "--force", "--yes", stdin="dukless\n")

        # then
        assert result.exit_code == 1 and calls.destroyed == []

    def test_given_catalog_force_and_confirmed_name_when_destroying_then_deleted(self, run) -> None:
        # when
        result, calls = run(destroy_plan("duckless", LAKE, False), "--force", "--confirm-name", "duckless")

        # then
        assert calls.destroyed == ["duckless"]
        assert "DuckLake catalog" in result.output

    def test_given_plain_install_and_yes_when_destroying_then_deleted_without_prompt(self, run) -> None:
        # when
        _, calls = run(destroy_plan("duckless", PLAIN, False), "--yes")

        # then
        assert calls.destroyed == ["duckless"]


class TestInterruptedDestroy:
    def test_given_no_deployment_but_leftovers_when_planning_then_allowed_and_explained(self) -> None:
        # given
        plan = destroy_plan("duckless", None, False, leftovers=True)

        # when / then
        assert destroy_blockers(plan, force=False) == ()
        assert "already gone" in destroy_summary(plan)[0]

    def test_given_no_deployment_and_no_leftovers_when_planning_then_refused(self) -> None:
        # when / then
        assert destroy_blockers(destroy_plan("duckless", None, False, leftovers=False), force=True)

    def test_given_deployment_gone_when_destroying_then_infra_account_and_staging_removed(self) -> None:
        # given: the deployment was deleted, the infra account was left with its roles
        from duckless import service
        from tests.test_infra import REQUEST, SA, FakeBootstrap, FakeDeployer

        bootstrap, deployer = FakeBootstrap(), FakeDeployer(current=None)

        # when
        status = service.destroy_infra(REQUEST, bootstrap=bootstrap, deployer=deployer, on_step=lambda s: None)

        # then: no deployment deletion, no re-grant, but revoke and delete
        assert status.state == "DELETED" and deployer.destroyed == []
        assert not any(call.startswith("grant") for call in bootstrap.calls)
        assert f"delete-sa:{SA}" in bootstrap.calls and "delete-bucket:acme-data-duckless-infra" in bootstrap.calls
        assert f"revoke:serviceAccount:{SA}" in bootstrap.calls

    def test_given_leftovers_and_force_when_destroying_then_no_reinit(self, monkeypatch) -> None:
        # given
        inits: list[str] = []
        destroyed: list[str] = []
        plan = destroy_plan("duckless", None, False, leftovers=True)
        monkeypatch.setattr(
            cli,
            "_infra",
            lambda: InfraServices(
                init=lambda r, s: inits.append(r.name) or InfraStatus("d", "ACTIVE"),
                destroy=lambda r, s: destroyed.append(r.name) or InfraStatus("d", "DELETED"),
                plan_destroy=lambda r: plan,
            ),
        )

        # when
        CliRunner().invoke(cli.app, ["destroy", "--project", "acme", "--force", "--yes"])

        # then: finishing a destroy must not re-create the installation
        assert inits == [] and destroyed == ["duckless"]
