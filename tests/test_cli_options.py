"""--project / --region work before or after the command name (`duckless init --project p`)."""

import pytest
from typer.testing import CliRunner

from duckless import cli
from duckless.core.infra import InfraRequest, InfraStatus
from duckless.wiring import InfraServices

ENV = {"DUCKLESS_BUCKET": "b", "DUCKLESS_SA": "sa", "DUCKLESS_IMAGE": "img"}


@pytest.fixture
def init_requests(monkeypatch: pytest.MonkeyPatch) -> list[InfraRequest]:
    requests: list[InfraRequest] = []

    def init(request: InfraRequest, on_step) -> InfraStatus:
        requests.append(request)
        return InfraStatus("d", "ACTIVE", {"envrc": "export DUCKLESS_PROJECT=p"})

    monkeypatch.setattr(
        cli, "_infra", lambda: InfraServices(init=init, destroy=lambda r, s: InfraStatus("d", "DELETED"))
    )
    return requests


@pytest.fixture
def settings_projects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    projects: list[str] = []

    class Services:
        def get_job(self, job_id):
            raise SystemExit(0)

    def fake(settings):
        projects.append(settings.project)
        return Services()

    monkeypatch.setattr(cli, "gcp_services", fake)
    return projects


class TestProjectAndRegionOptions:
    def test_given_options_after_init_when_running_then_request_uses_them(
        self, init_requests: list[InfraRequest]
    ) -> None:
        # when
        result = CliRunner().invoke(cli.app, ["init", "--project", "p1", "--region", "us-central1"], env={})

        # then
        assert result.exit_code == 0, result.output
        assert (init_requests[0].project, init_requests[0].region) == ("p1", "us-central1")

    def test_given_global_and_command_project_when_running_then_command_level_wins(
        self, init_requests: list[InfraRequest]
    ) -> None:
        # when
        result = CliRunner().invoke(cli.app, ["--project", "global", "init", "--project", "local"], env={})

        # then
        assert result.exit_code == 0, result.output
        assert init_requests[0].project == "local"

    def test_given_global_project_only_when_running_then_it_is_used(self, init_requests: list[InfraRequest]) -> None:
        # when
        CliRunner().invoke(cli.app, ["--project", "global", "init"], env={})

        # then
        assert init_requests[0].project == "global"

    def test_given_project_after_status_when_running_then_job_settings_use_it(
        self, settings_projects: list[str]
    ) -> None:
        # when
        CliRunner().invoke(cli.app, ["status", "dl-job", "--project", "p2"], env=ENV)

        # then
        assert settings_projects == ["p2"]
