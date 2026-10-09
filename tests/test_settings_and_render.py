import pytest

from duckless.adapters.gcs import parse_metrics
from duckless.cli import google_error_line, last_preview
from duckless.settings import Settings, SettingsError

ENV = {"DUCKLESS_PROJECT": "acme", "DUCKLESS_BUCKET": "work", "DUCKLESS_SA": "sa", "DUCKLESS_IMAGE": "img"}


class TestSettings:
    def test_given_missing_vars_when_loading_then_error_names_them(self) -> None:
        # when / then
        with pytest.raises(SettingsError, match="DUCKLESS_SA, DUCKLESS_IMAGE"):
            Settings.from_env({"DUCKLESS_PROJECT": "acme", "DUCKLESS_BUCKET": "work"})

    def test_given_cli_override_when_loading_then_override_wins_over_env(self) -> None:
        # when
        settings = Settings.from_env(ENV | {"DUCKLESS_REGION": "us-central1"}, region="europe-west4", project=None)

        # then
        assert settings.region == "europe-west4"
        assert settings.project == "acme"

    def test_given_full_subnetwork_path_when_loading_then_kept_as_is(self) -> None:
        # when
        settings = Settings.from_env(ENV | {"DUCKLESS_SUBNETWORK": "projects/host/regions/europe-west1/subnetworks/s"})

        # then
        assert settings.subnetwork == "projects/host/regions/europe-west1/subnetworks/s"


class TestRunnerOutputs:
    def test_given_json_lines_metrics_when_parsing_then_first_object_is_returned(self) -> None:
        # when / then
        assert parse_metrics('\n{"status": "succeeded"}\n') == {"status": "succeeded"}

    def test_given_steps_when_taking_preview_then_last_step_with_rows_wins(self) -> None:
        # given
        metrics = {"steps": [{"preview": [["1"]]}, {"preview": [["2"]]}, {"preview": []}]}

        # when / then
        assert last_preview(metrics) == [["2"]]


class TestGoogleErrors:
    def test_given_api_error_when_rendering_then_one_line_with_its_kind(self) -> None:
        # given
        from google.api_core.exceptions import ServiceUnavailable

        # when
        line = google_error_line(ServiceUnavailable("The service is currently unavailable.\nmore detail"))

        # then
        assert line.startswith("ServiceUnavailable: The service is currently unavailable.")
        assert "more detail" not in line

    def test_given_expired_credentials_when_rendering_then_says_how_to_log_in(self) -> None:
        # given
        from google.auth.exceptions import RefreshError

        # when / then
        assert "gcloud auth login --update-adc" in google_error_line(RefreshError("Reauthentication is needed."))
