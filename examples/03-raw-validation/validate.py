"""Validate a raw CSV against declared rules: Pydantic checks the rules, DuckDB checks the data.

    gcloud storage cp examples/03-raw-validation/rules.yaml gs://$DUCKLESS_BUCKET/examples/validation/
    duckless run examples/03-raw-validation/validate.py -m n2-standard-8 \\
      -e RULES=gs://$DUCKLESS_BUCKET/examples/validation/rules.yaml

Every rule is one SQL predicate run over the whole file, so the cost does not depend on the
number of rules much, and no Python touches a row. Lines that do not even parse are caught by the
CSV reader itself (`store_rejects`). The report (every error, plus a summary) goes to GCS; the job
fails when the error rate is above `max_error_rate`.
"""

import json
import os
import re
import time
from string import Template
from typing import Annotated

import yaml
from duckless_runtime import connect, log
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Strict(BaseModel):
    # A misspelt key ("matchs:") is an error, not a silently ignored rule.
    model_config = ConfigDict(extra="forbid")


# ---------- rules: Pydantic validates the configuration, never the rows ----------


def quoted(name: str) -> str:
    if not IDENTIFIER.match(name):
        raise ValueError(f"'{name}' is not a plain column name")
    return f'"{name}"'


def literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class Unique(Strict):
    name: str
    unique: str

    def failing(self) -> str:
        return f"count(*) OVER (PARTITION BY {quoted(self.unique)}) > 1"


class NotNull(Strict):
    name: str
    not_null: str

    def failing(self) -> str:
        column = quoted(self.not_null)
        return f"{column} IS NULL OR trim(CAST({column} AS VARCHAR)) = ''"


class Matches(Strict):
    name: str
    column: str
    matches: str

    @field_validator("matches")
    @classmethod
    def compiles(cls, pattern: str) -> str:
        try:
            re.compile(pattern)  # a broken regex fails here, before reading any data
        except re.error as e:
            raise ValueError(f"invalid regular expression: {e}") from e
        return pattern

    def failing(self) -> str:
        return f"NOT coalesce(regexp_matches({quoted(self.column)}, {literal(self.matches)}), false)"


class OneOf(Strict):
    name: str
    column: str
    one_of: list[str] = Field(min_length=1)

    def failing(self) -> str:
        values = ", ".join(literal(v) for v in self.one_of)
        return f"{quoted(self.column)} IS NULL OR {quoted(self.column)} NOT IN ({values})"


class Check(Strict):
    name: str
    check: str  # a SQL boolean expression over the columns, written by the data team

    def failing(self) -> str:
        return f"NOT coalesce(({self.check}), false)"


Rule = Annotated[Unique | NotNull | Matches | OneOf | Check, Field(union_mode="left_to_right")]


class Source(Strict):
    path: str
    columns: dict[str, str] = Field(min_length=1)
    key: str

    @model_validator(mode="after")
    def key_is_a_column(self) -> "Source":
        if self.key not in self.columns:
            raise ValueError(f"key '{self.key}' is not one of the columns")
        return self


class Rules(Strict):
    source: Source
    report: str
    max_error_rate: float = Field(ge=0, le=1)
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def rules_use_declared_columns(self) -> "Rules":
        for rule in self.rules:
            for field in ("unique", "not_null", "column"):
                column = getattr(rule, field, None)
                if column is not None and column not in self.source.columns:
                    raise ValueError(f"rule '{rule.name}' uses '{column}', not a column of the source")
        return self


# ---------- SQL built from the rules: pure ----------


def read_source_sql(source: Source) -> str:
    columns = "{" + ", ".join(f"{literal(c)}: {literal(t)}" for c, t in source.columns.items()) + "}"
    return (
        "CREATE TABLE source AS SELECT * FROM read_csv("
        f"{literal(source.path)}, header = true, columns = {columns}, store_rejects = true)"
    )


def violations_sql(rules: Rules) -> str:
    key = quoted(rules.source.key)
    flags = ",\n  ".join(f"{rule.failing()} AS {quoted(rule.name)}" for rule in rules.rules)
    names = ", ".join(quoted(rule.name) for rule in rules.rules)
    return f"""
CREATE TABLE violations AS
WITH flagged AS (SELECT *, {flags} FROM source)
SELECT rule, CAST({key} AS VARCHAR) AS record_key, NULL::VARCHAR AS detail
FROM flagged UNPIVOT (failed FOR rule IN ({names}))
WHERE failed
UNION ALL
SELECT 'unparsable_line', CAST(line AS VARCHAR), error_message FROM reject_errors
"""


# ---------- job ----------


def main() -> None:
    con = connect()
    text = con.sql(f"SELECT content FROM read_text({literal(os.environ['RULES'])})").fetchone()[0]
    rules = Rules.model_validate(yaml.safe_load(Template(text).safe_substitute(os.environ)))
    log("rules_valid", rules=len(rules.rules), source=rules.source.path)

    started = time.perf_counter()
    con.sql(read_source_sql(rules.source))
    con.sql(violations_sql(rules))
    lines = con.sql("SELECT count(*) FROM source").fetchone()[0]
    rejected = con.sql("SELECT count(*) FROM reject_errors").fetchone()[0]
    checked_in = round(time.perf_counter() - started, 2)

    con.sql(f"COPY violations TO {literal(rules.report + '/errors')} (FORMAT parquet, PER_THREAD_OUTPUT, OVERWRITE)")
    per_rule = dict(con.sql("SELECT rule, count(*) FROM violations GROUP BY ALL ORDER BY ALL").fetchall())
    bad_lines = (
        con.sql("SELECT count(DISTINCT record_key) FROM violations WHERE rule <> 'unparsable_line'").fetchone()[0]
        + rejected
    )
    error_rate = bad_lines / max(lines + rejected, 1)
    summary = {
        "source": rules.source.path,
        "lines": lines + rejected,
        "lines_with_errors": bad_lines,
        "error_rate": round(error_rate, 6),
        "max_error_rate": rules.max_error_rate,
        "errors_per_rule": per_rule,
        "checked_in_seconds": checked_in,
    }
    summary_uri = literal(rules.report + "/summary.json")
    con.sql(f"COPY (SELECT {literal(json.dumps(summary))}::JSON AS summary) TO {summary_uri}")
    log("validation_summary", **summary)

    if error_rate > rules.max_error_rate:
        raise RuntimeError(f"error rate {error_rate:.4%} is above {rules.max_error_rate:.4%}: see {rules.report}")


if __name__ == "__main__":
    main()
