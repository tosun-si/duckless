import duckdb
import pytest
from duckless_runtime.bigquery_write import (
    Partitioning,
    Target,
    check_request,
    data_checks_sql,
    data_problems,
    empty_write_problem,
    merge_sql,
    replace_partitions_sql,
    should_prune,
    staging_prefix,
)

DAY = Partitioning("posting_date", "DATE", "DAY")
MONTH_TS = Partitioning("created_at", "TIMESTAMP", "MONTH")
COLUMNS = ("posting_date", "entry_id", "line_no", "amount")
TABLE = Target(True, COLUMNS, DAY)


class TestCheckRequest:
    def test_given_a_valid_merge_when_checking_then_nothing_to_report(self) -> None:
        # when / then
        assert check_request("merge", TABLE, COLUMNS, ("entry_id", "line_no")) == ()

    def test_given_an_unknown_mode_when_checking_then_refused(self) -> None:
        # when / then
        assert "mode must be one of" in check_request("upsert", TABLE, COLUMNS, ())[0]

    @pytest.mark.parametrize("mode", ["merge", "replace_partitions"])
    def test_given_no_table_when_staging_mode_then_refused(self, mode: str) -> None:
        # when
        problems = check_request(mode, Target(False), COLUMNS, ("entry_id",))

        # then
        assert any("needs an existing table" in p for p in problems)

    def test_given_merge_without_key_when_checking_then_refused(self) -> None:
        # when / then
        assert any("merge needs key" in p for p in check_request("merge", TABLE, COLUMNS, ()))

    def test_given_columns_the_table_lacks_when_checking_then_named(self) -> None:
        # when
        problems = check_request("replace_partitions", TABLE, (*COLUMNS, "comment"), ())

        # then
        assert problems == ("columns missing from the table: comment",)

    def test_given_ingestion_time_table_when_replacing_partitions_then_the_reason(self) -> None:
        # given
        table = Target(True, COLUMNS, None, "the table is partitioned by ingestion time: use merge")

        # when / then
        assert check_request("replace_partitions", table, COLUMNS, ()) == (table.partitioning_issue,)

    def test_given_data_without_the_partition_column_when_replacing_then_refused(self) -> None:
        # when / then
        assert "partition column posting_date" in check_request("replace_partitions", TABLE, COLUMNS[1:], ())[0]

    @pytest.mark.parametrize("mode", ["append", "replace"])
    def test_given_no_table_when_load_mode_then_created(self, mode: str) -> None:
        # when / then
        assert check_request(mode, Target(False), COLUMNS, ()) == ()

    def test_given_a_key_outside_merge_when_checking_then_refused(self) -> None:
        # when / then
        assert check_request("append", TABLE, COLUMNS, ("entry_id",)) == ("key is only used by merge, not by append",)


class TestReplacePartitionsSql:
    def test_given_daily_date_partitions_when_building_then_deletes_whole_partitions_in_a_transaction(self) -> None:
        # when
        sql = replace_partitions_sql("`p.d.t`", "`p.d.s`", COLUMNS, DAY)

        # then: the exact DELETE measured free (whole partitions, partition column only)
        assert (
            "DECLARE touched DEFAULT (SELECT AS STRUCT ARRAY_AGG(DISTINCT s.`posting_date` IGNORE NULLS) AS parts FROM"
            in sql
        )
        assert "DELETE FROM `p.d.t` t WHERE t.`posting_date` IN UNNEST(touched.parts);" in sql
        assert sql.count("FROM `p.d.s`") == 2  # the staging table is read twice: BigQuery's 10 MB floor each
        assert sql.index("BEGIN TRANSACTION") < sql.index("DELETE") < sql.index("INSERT") < sql.index("COMMIT")
        assert "INSERT INTO `p.d.t` (`posting_date`, `entry_id`, `line_no`, `amount`) SELECT" in sql

    def test_given_monthly_timestamp_partitions_when_building_then_bounds_on_the_raw_column(self) -> None:
        # when
        sql = replace_partitions_sql("`p.d.t`", "`p.d.s`", ("created_at", "id"), MONTH_TS)

        # then: bounds let BigQuery prune; the IN keeps whole touched months only
        assert (
            "t.`created_at` >= touched.lo AND t.`created_at` < touched.hi AND TIMESTAMP_TRUNC(t.`created_at`, MONTH)"
            in sql
        )
        assert "TIMESTAMP(DATETIME_ADD(DATETIME(MAX(TIMESTAMP_TRUNC(s.`created_at`, MONTH))), INTERVAL 1 MONTH))" in sql


class TestMergeSql:
    def test_given_prune_when_building_then_target_read_only_in_touched_partitions(self) -> None:
        # when
        sql = merge_sql("`p.d.t`", "`p.d.s`", COLUMNS, ("entry_id", "line_no"), DAY, prune=True)

        # then
        assert sql.startswith("DECLARE touched")
        on = (
            "ON t.`posting_date` IN UNNEST(touched.parts) AND t.`entry_id` = s.`entry_id` AND t.`line_no` = s.`line_no`"
        )
        assert on in sql
        assert "UPDATE SET `posting_date` = s.`posting_date`, `amount` = s.`amount`" in sql

    def test_given_no_prune_when_building_then_plain_merge_on_the_key(self) -> None:
        # when
        sql = merge_sql("`p.d.t`", "`p.d.s`", COLUMNS, ("entry_id", "line_no"), DAY, prune=False)

        # then
        assert "DECLARE" not in sql and "ON t.`entry_id` = s.`entry_id` AND" in sql

    def test_given_every_column_in_the_key_when_building_then_insert_only(self) -> None:
        # when
        sql = merge_sql("`p.d.t`", "`p.d.s`", ("a", "b"), ("a", "b"), None, prune=False)

        # then
        assert "WHEN MATCHED" not in sql and "WHEN NOT MATCHED THEN INSERT" in sql


class TestShouldPrune:
    @pytest.mark.parametrize(
        ("key", "prune", "expected"),
        [
            (("posting_date", "entry_id"), None, True),  # partition column in the key: a row can't move
            (("entry_id",), None, False),  # could move partition: read everything
            (("entry_id",), True, True),  # the caller says it never moves
            (("posting_date", "entry_id"), False, False),
        ],
    )
    def test_given_key_and_choice_when_deciding_then_pruned_only_when_safe(self, key, prune, expected) -> None:
        # when / then
        assert should_prune(DAY, key, prune) is expected

    def test_given_unpartitioned_table_when_deciding_then_never(self) -> None:
        # when / then
        assert should_prune(None, ("a",), True) is False


class TestDataChecks:
    def test_given_bad_rows_when_checking_in_duckdb_then_each_problem_counted(self, tmp_path) -> None:
        # given: a duplicated key, a NULL key, a NULL partition value
        con = duckdb.connect()
        path = tmp_path / "rows.parquet"
        con.sql(f"""COPY (SELECT * FROM (VALUES (DATE '2026-09-30', 1, 1), (DATE '2026-09-30', 1, 1),
                     (NULL, 2, NULL)) v(posting_date, entry_id, line_no)) TO '{path}' (FORMAT parquet)""")

        # when
        sql = data_checks_sql(str(path), "merge", ("entry_id", "line_no"), "posting_date")
        row = con.sql(sql)
        problems = data_problems(dict(zip(row.columns, row.fetchone(), strict=True)))

        # then
        assert problems == (
            "1 rows share a key: a MERGE needs one source row per key",
            "1 rows have a NULL key: they would never match and be inserted again on every run",
            "1 rows have a NULL partition column: no partition would be replaced or read for them",
        )

    def test_given_append_when_checking_then_nothing_to_check(self) -> None:
        # when / then
        assert data_checks_sql("x", "append", (), None) is None


class TestStagingPrefix:
    def test_given_a_duckless_job_when_choosing_then_under_its_work_bucket(self) -> None:
        # when
        prefix = staging_prefix("dl-close-1", "gs://acme-work/runs/dl-close-1/metrics.json", None)

        # then: under the job's folder, which the bucket's lifecycle rule cleans
        assert prefix.startswith("gs://acme-work/runs/dl-close-1/bigquery/")

    def test_given_an_explicit_prefix_when_choosing_then_used(self) -> None:
        # when / then
        assert staging_prefix(None, None, "gs://b/tmp/").startswith("gs://b/tmp/local-")

    def test_given_no_bucket_known_when_choosing_then_says_what_to_pass(self) -> None:
        # when / then
        with pytest.raises(ValueError, match="staging_uri"):
            staging_prefix(None, None, None)


class TestEmptyWrite:
    def test_given_no_rows_when_replacing_then_refused(self) -> None:
        # when / then: an upstream bug must not empty the table
        assert "would empty the table" in empty_write_problem("replace", 0, allow_empty=False)

    @pytest.mark.parametrize(
        ("mode", "rows", "allow_empty"),
        [("replace", 0, True), ("replace", 5, False), ("append", 0, False), ("replace_partitions", 0, False)],
    )
    def test_given_other_cases_when_checking_then_allowed(self, mode: str, rows: int, allow_empty: bool) -> None:
        # when / then
        assert empty_write_problem(mode, rows, allow_empty) is None
