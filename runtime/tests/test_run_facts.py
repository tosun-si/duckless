import hashlib

import duckdb
import pytest
from duckless_runtime import bigquery_write
from duckless_runtime.__main__ import run_job, source_sha256, versions


class TestRunFacts:
    def test_given_a_source_when_fingerprinting_then_sha256_of_its_text(self) -> None:
        # when / then
        assert source_sha256("SELECT 1;") == hashlib.sha256(b"SELECT 1;").hexdigest()

    def test_given_a_connection_when_reading_versions_then_duckdb_runtime_and_loaded_extensions(
        self, monkeypatch
    ) -> None:
        # given
        monkeypatch.setenv("DUCKLESS_CLI_VERSION", "0.4.3")
        con = duckdb.connect()
        con.sql("LOAD json")

        # when
        found = versions(con)

        # then
        assert found["cli"] == "0.4.3" and found["duckdb"] == duckdb.__version__ and found["runtime"]
        assert "json" in found["extensions"]

    def test_given_a_sql_job_when_running_then_its_fingerprint_is_kept_even_when_it_fails(self, tmp_path) -> None:
        # given
        job = tmp_path / "job.sql"
        job.write_text("SELECT * FROM missing_table;")

        # when
        outcome = run_job(duckdb.connect(), "sql", str(job), [])

        # then
        assert outcome["status"] == "failed"
        assert outcome["source_sha256"] == source_sha256(job.read_text())


class TestBigQueryWrites:
    def test_given_writes_when_listing_then_each_one_in_order(self, monkeypatch) -> None:
        # given
        monkeypatch.setattr(bigquery_write, "_WRITES", [{"table": "p.d.a"}, {"table": "p.d.b"}])

        # when / then
        assert [w["table"] for w in bigquery_write.written_to_bigquery()] == ["p.d.a", "p.d.b"]


class TestStageLocally:
    def test_given_a_missing_object_when_staging_then_a_clear_error(self, monkeypatch) -> None:
        # given: a GCS client that finds nothing
        import pyarrow.fs as pafs
        from duckless_runtime import staging

        class NoObject:
            def get_file_info(self, path):
                return pafs.FileInfo(path, pafs.FileType.NotFound)

        monkeypatch.setattr(pafs, "GcsFileSystem", NoObject)

        # when / then
        with pytest.raises(FileNotFoundError, match=r"gs://b/missing\.csv does not exist"):
            staging.stage_locally("gs://b/missing.csv")
