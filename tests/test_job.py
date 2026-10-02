from datetime import UTC, datetime

import pytest

from duckless.core.errors import InvalidJobError, InvalidLocalSsdCountError
from duckless.core.job import JobKind, JobSpec, JobState, job_id_is_valid, new_job_id
from duckless.core.machine import MachineType

NOW = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)


def spec(**overrides) -> JobSpec:
    return JobSpec(
        **{
            "job_id": "dl-job-1",
            "kind": JobKind.SQL,
            "image": "img",
            "machine": MachineType.parse("n2-highmem-16"),
            "spot": False,
            "local_ssd_count": 2,
            "max_run_seconds": 60,
            "source_uri": "gs://b/job.sql",
        }
        | overrides
    )


class TestNewJobId:
    def test_given_name_with_symbols_when_generating_then_id_is_a_valid_batch_id(self) -> None:
        # when
        job_id = new_job_id("Daily_Revenue (v2)", NOW, "ABCDEF")

        # then
        assert job_id == "dl-daily-revenue--v2-20261002-093000-abcd"
        assert job_id_is_valid(job_id)

    def test_given_very_long_name_when_generating_then_id_fits_63_chars(self) -> None:
        # when
        job_id = new_job_id("x" * 200, NOW, "abcd")

        # then
        assert len(job_id) <= 63
        assert job_id_is_valid(job_id)


class TestJobSpec:
    def test_given_sql_job_when_building_args_then_runner_gets_kind_and_uri(self) -> None:
        # when / then
        assert spec().runner_args == ("sql", "gs://b/job.sql")

    def test_given_command_job_when_building_args_then_command_is_passed_as_is(self) -> None:
        # when / then
        assert spec(kind=JobKind.COMMAND, source_uri=None, command=("dbt", "build")).runner_args == ("dbt", "build")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"job_id": "Bad_Id"},
            {"kind": JobKind.COMMAND, "source_uri": None},
            {"source_uri": None},
            {"max_run_seconds": 0},
        ],
        ids=["bad-id", "command-without-command", "sql-without-source", "no-duration"],
    )
    def test_given_inconsistent_fields_when_building_then_raises(self, overrides: dict) -> None:
        # when / then
        with pytest.raises(InvalidJobError):
            spec(**overrides)

    def test_given_disallowed_local_ssd_count_when_building_then_raises(self) -> None:
        # when / then
        with pytest.raises(InvalidLocalSsdCountError):
            spec(local_ssd_count=1)


class TestJobState:
    @pytest.mark.parametrize("state", [JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED])
    def test_given_final_state_when_checking_then_terminal(self, state: JobState) -> None:
        # when / then
        assert state.is_terminal

    @pytest.mark.parametrize("state", [JobState.QUEUED, JobState.SCHEDULED, JobState.RUNNING, JobState.UNKNOWN])
    def test_given_live_state_when_checking_then_not_terminal(self, state: JobState) -> None:
        # when / then
        assert not state.is_terminal
