"""Unit tests of the validator, on DuckDB in memory: the same engine as on the runner, no cloud.

cd runtime && uv run --with pytest pytest ../examples
"""

import importlib.util
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

spec = importlib.util.spec_from_file_location("validate", Path(__file__).with_name("validate.py"))
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)

SOURCE = {"path": "x.csv", "columns": {"customer_id": "BIGINT", "email": "VARCHAR"}, "key": "customer_id"}


def rules(*rule_dicts: dict) -> validate.Rules:
    return validate.Rules.model_validate(
        {"source": SOURCE, "report": "out", "max_error_rate": 0.01, "rules": list(rule_dicts)}
    )


def failing_ids(rule, rows: list[tuple[int, str | None]]) -> list[int]:
    con = duckdb.connect()
    con.sql("CREATE TABLE source (customer_id BIGINT, email VARCHAR)")
    con.executemany("INSERT INTO source VALUES (?, ?)", rows)
    # As the validator does: the rule is a column (a window function cannot sit in a WHERE).
    flagged = f"SELECT customer_id, {rule.failing()} AS failed FROM source"
    return [r[0] for r in con.sql(f"SELECT customer_id FROM ({flagged}) WHERE failed ORDER BY 1").fetchall()]


class TestRulesFile:
    def test_given_valid_rules_when_parsing_then_each_rule_gets_its_kind(self) -> None:
        # when
        parsed = rules({"name": "u", "unique": "customer_id"}, {"name": "f", "column": "email", "matches": "@"})

        # then
        assert [type(r).__name__ for r in parsed.rules] == ["Unique", "Matches"]

    @pytest.mark.parametrize(
        "bad_rule",
        [
            {"name": "typo", "column": "email", "matchs": "@"},  # misspelt key
            {"name": "ghost", "not_null": "phone"},  # column not in the source
            {"name": "regex", "column": "email", "matches": "(["},  # broken regex
        ],
    )
    def test_given_bad_rule_when_parsing_then_rejected_before_reading_data(self, bad_rule: dict) -> None:
        # when / then
        with pytest.raises(ValidationError):
            rules(bad_rule)


class TestRulesOnData:
    def test_given_bad_email_when_checking_then_only_that_row_fails(self) -> None:
        # given
        rule = validate.Matches(name="email_format", column="email", matches=r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")

        # when / then
        assert failing_ids(rule, [(1, "a@b.fr"), (2, "no-at-sign"), (3, None)]) == [2, 3]

    def test_given_duplicate_keys_when_checking_unique_then_every_copy_fails(self) -> None:
        # when / then
        assert failing_ids(validate.Unique(name="u", unique="customer_id"), [(1, "a"), (2, "b"), (2, "c")]) == [2, 2]

    def test_given_blank_value_when_checking_not_null_then_it_fails(self) -> None:
        # when / then
        assert failing_ids(validate.NotNull(name="n", not_null="email"), [(1, "x"), (2, ""), (3, None)]) == [2, 3]

    def test_given_check_expression_when_unknown_then_it_fails(self) -> None:
        # given: NULL > 0 is unknown, which must count as a failure
        rule = validate.Check(name="c", check="length(email) > 1")

        # when / then
        assert failing_ids(rule, [(1, "ab"), (2, "a"), (3, None)]) == [2, 3]

    def test_given_column_name_with_sql_when_quoting_then_refused(self) -> None:
        # when / then
        with pytest.raises(ValueError):
            validate.quoted('email"; DROP TABLE source; --')
