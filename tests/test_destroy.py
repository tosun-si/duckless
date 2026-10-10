from dataclasses import dataclass, field

import pytest
from typer.testing import CliRunner

from duckless import cli
from duckless.core.infra import DestroyPlan, InfraStatus, destroy_blockers, destroy_plan, needs_typed_name
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
