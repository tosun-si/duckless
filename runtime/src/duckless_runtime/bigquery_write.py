"""Writing a DuckDB result to BigQuery: Parquet on GCS, then a load job, then SQL inside BigQuery.

A load job is free and atomic, and reads the Parquet files in parallel: it is how BigQuery
itself recommends loading in bulk. The four modes differ in what happens after the load:

- append, replace: the load job writes the target table directly (created when missing).
- replace_partitions: the load fills a staging table; one transaction deletes the target's
  partitions the data touches and inserts the new rows. A DELETE of whole partitions is
  metadata only, so BigQuery bills the staging rows read by the INSERT, never the target.
- merge: the load fills a staging table; one MERGE on the key. When the partition column is part
  of the key (or `prune=True` says a row never changes partition), the MERGE reads only the
  partitions the data touches, computed into script variables first: BigQuery does not prune on a
  subquery.

The staging table lives in the target's dataset, takes the target's column types (a type
mismatch fails before the target is touched), expires after a day and is dropped at the end.
"""

import contextlib
import os
import time
import uuid
from dataclasses import dataclass, field

import duckdb

from duckless_runtime.bigquery import table_path

MODES = ("append", "replace", "replace_partitions", "merge")
STAGING_MODES = frozenset({"replace_partitions", "merge"})
STAGING_TTL_SECONDS = 24 * 3600
TIME_UNITS = frozenset({"HOUR", "DAY", "MONTH", "YEAR"})
TRUNC = {"DATE": "DATE_TRUNC", "DATETIME": "DATETIME_TRUNC", "TIMESTAMP": "TIMESTAMP_TRUNC"}


@dataclass(frozen=True)
class Partitioning:
    """The target's column partitioning: `column` of BigQuery type `type`, by `unit`."""

    column: str
    type: str  # DATE, DATETIME, TIMESTAMP
    unit: str  # HOUR, DAY, MONTH, YEAR


@dataclass(frozen=True)
class Target:
    exists: bool
    columns: tuple[str, ...] = ()
    partitioning: Partitioning | None = None
    # Why the target can't be used for partition replacement (ingestion time, integer range).
    partitioning_issue: str | None = None


@dataclass(frozen=True)
class WriteResult:
    table: str
    mode: str
    rows: int
    bytes_billed: int
    seconds: dict[str, float] = field(default_factory=dict)


def quote(name: str) -> str:
    return f"`{name.replace('`', '')}`"


def check_request(mode: str, target: Target, source: tuple[str, ...], key: tuple[str, ...]) -> tuple[str, ...]:
    """Every reason the write can't run as asked, before anything is written."""
    if mode not in MODES:
        return (f"mode must be one of {', '.join(MODES)}, not '{mode}'",)
    problems = []
    if mode in STAGING_MODES:
        if not target.exists:
            problems.append(f"{mode} needs an existing table: it defines the partitioning and the column types")
        extra = [c for c in source if c not in target.columns]
        if target.exists and extra:
            problems.append(f"columns missing from the table: {', '.join(extra)}")
    if mode == "merge":
        if not key:
            problems.append("merge needs key=[...], the columns that identify a row")
        missing = [c for c in key if c not in source]
        if missing:
            problems.append(f"key columns missing from the data: {', '.join(missing)}")
    if mode == "replace_partitions" and target.exists:
        if target.partitioning is None:
            problems.append(target.partitioning_issue or "replace_partitions needs a table partitioned by a column")
        elif target.partitioning.column not in source:
            problems.append(f"the partition column {target.partitioning.column} is missing from the data")
    if key and mode != "merge":
        problems.append(f"key is only used by merge, not by {mode}")
    return tuple(problems)


def partition_of(partitioning: Partitioning, alias: str) -> str:
    """The partition a row belongs to, as an expression on `alias`."""
    column = f"{alias}.{quote(partitioning.column)}"
    if partitioning.type == "DATE" and partitioning.unit == "DAY":
        return column
    return f"{TRUNC[partitioning.type]}({column}, {partitioning.unit})"


def next_partition(partitioning: Partitioning, value: str) -> str:
    """The start of the partition after the one starting at `value` (TIMESTAMP_ADD has no MONTH)."""
    unit = partitioning.unit
    if partitioning.type == "DATE":
        return f"DATE_ADD({value}, INTERVAL 1 {unit})"
    if partitioning.type == "DATETIME":
        return f"DATETIME_ADD({value}, INTERVAL 1 {unit})"
    return f"TIMESTAMP(DATETIME_ADD(DATETIME({value}), INTERVAL 1 {unit}))"


def is_daily_date(partitioning: Partitioning) -> bool:
    """The column value is the partition: pruning needs no bounds."""
    return partitioning.type == "DATE" and partitioning.unit == "DAY"


def touched_partitions_sql(partitioning: Partitioning, staging: str) -> str:
    """One script variable: the partitions the data touches, and their bounds when pruning needs them.

    One statement reading the staging table: BigQuery bills at least 10 MB per table read by each
    statement, so separate DECLAREs would each add that floor.
    """
    part = partition_of(partitioning, "s")
    fields = [f"ARRAY_AGG(DISTINCT {part} IGNORE NULLS) AS parts"]
    if not is_daily_date(partitioning):
        fields += [f"MIN({part}) AS lo", f"{next_partition(partitioning, f'MAX({part})')} AS hi"]
    return f"DECLARE touched DEFAULT (SELECT AS STRUCT {', '.join(fields)} FROM {staging} s);\n"


def in_touched_partitions(partitioning: Partitioning, alias: str) -> str:
    """Bounds on the raw column, so BigQuery prunes; the IN keeps only the touched partitions."""
    column = f"{alias}.{quote(partitioning.column)}"
    bounds = "" if is_daily_date(partitioning) else f"{column} >= touched.lo AND {column} < touched.hi AND "
    return f"{bounds}{partition_of(partitioning, alias)} IN UNNEST(touched.parts)"


def replace_partitions_sql(target: str, staging: str, columns: tuple[str, ...], partitioning: Partitioning) -> str:
    names = ", ".join(quote(c) for c in columns)
    return (
        touched_partitions_sql(partitioning, staging)
        + "BEGIN TRANSACTION;\n"
        + f"DELETE FROM {target} t WHERE {in_touched_partitions(partitioning, 't')};\n"
        + f"INSERT INTO {target} ({names}) SELECT {names} FROM {staging};\n"
        + "COMMIT TRANSACTION;\n"
    )


def merge_sql(
    target: str,
    staging: str,
    columns: tuple[str, ...],
    key: tuple[str, ...],
    partitioning: Partitioning | None,
    prune: bool,
) -> str:
    """One MERGE on the key, reading only the touched partitions of the target when `prune`."""
    prune = prune and partitioning is not None
    on = " AND ".join(f"t.{quote(c)} = s.{quote(c)}" for c in key)
    if prune:
        on = f"{in_touched_partitions(partitioning, 't')} AND {on}"
    updates = ", ".join(f"{quote(c)} = s.{quote(c)}" for c in columns if c not in key)
    names = ", ".join(quote(c) for c in columns)
    values = ", ".join(f"s.{quote(c)}" for c in columns)
    matched = f"WHEN MATCHED THEN UPDATE SET {updates}\n" if updates else ""
    return (
        (touched_partitions_sql(partitioning, staging) if prune else "")
        + f"MERGE {target} t USING {staging} s\nON {on}\n"
        + matched
        + f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values});\n"
    )


def should_prune(partitioning: Partitioning | None, key: tuple[str, ...], prune: bool | None) -> bool:
    """Pruning is safe when a row can't move to another partition: the partition column is in the
    key, or the caller says so (`prune=True`). Otherwise a row whose partition value changed would
    not be found in its old partition, and would be inserted a second time."""
    if partitioning is None or prune is False:
        return False
    return prune is True or partitioning.column in key


def data_checks_sql(parquet: str, mode: str, key: tuple[str, ...], partition_column: str | None) -> str | None:
    """`partition_column`: set when rows must have one (replace_partitions, a pruned merge)."""
    """One pass in DuckDB over the written files: what BigQuery would reject, or silently get wrong."""
    checks = []
    if mode == "merge":
        keys = [f'"{c}"' for c in key]
        checks.append(f"count(*) - count(DISTINCT ({', '.join(keys)})) AS duplicate_keys")
        checks.append(f"count(*) FILTER (WHERE {' OR '.join(f'{k} IS NULL' for k in keys)}) AS null_keys")
    if partition_column:
        checks.append(f'count(*) FILTER (WHERE "{partition_column}" IS NULL) AS null_partition_values')
    if not checks:
        return None
    return f"SELECT {', '.join(checks)} FROM read_parquet('{parquet}')"


def data_problems(counts: dict[str, int]) -> tuple[str, ...]:
    messages = {
        "duplicate_keys": "rows share a key: a MERGE needs one source row per key",
        "null_keys": "rows have a NULL key: they would never match and be inserted again on every run",
        "null_partition_values": "rows have a NULL partition column: no partition would be replaced or read for them",
    }
    return tuple(f"{n} {messages[name]}" for name, n in counts.items() if n)


def empty_write_problem(mode: str, rows: int, allow_empty: bool) -> str | None:
    """An empty `replace` empties the table: most often an upstream bug (a missing file), never by default."""
    if mode == "replace" and rows == 0 and not allow_empty:
        return "no rows to write: replace would empty the table (allow_empty=True if that is the intent)"
    return None


def staging_prefix(job_id: str | None, metrics_uri: str | None, explicit: str | None) -> str:
    """Where the Parquet files go: the explicit prefix, else the job's folder of the work bucket
    (`runs/<job>/`, whose lifecycle rule removes what a VM lost mid-write left behind)."""
    write = uuid.uuid4().hex[:8]
    if explicit:
        return f"{explicit.rstrip('/')}/{job_id or 'local'}-{write}"
    if metrics_uri and metrics_uri.startswith("gs://"):
        return f"{metrics_uri.rsplit('/', 1)[0]}/bigquery/{write}"
    raise ValueError("no work bucket known outside a DuckLess job: pass staging_uri='gs://my-bucket/some/prefix'")


# ---------- BigQuery side (the only part with a client) ----------


def _target(client, table: str) -> Target:
    from google.api_core.exceptions import NotFound

    try:
        t = client.get_table(table)
    except NotFound:
        return Target(exists=False)
    columns = tuple(f.name for f in t.schema)
    types = {f.name: f.field_type for f in t.schema}
    if t.range_partitioning is not None:
        return Target(True, columns, None, "replace_partitions does not support integer range partitioning yet")
    tp = t.time_partitioning
    if tp is None:
        return Target(True, columns, None, "the table is not partitioned: use replace or merge")
    if tp.field is None:
        return Target(True, columns, None, "the table is partitioned by ingestion time: use merge")
    kind = {"TIMESTAMP": "TIMESTAMP", "DATETIME": "DATETIME", "DATE": "DATE"}.get(types[tp.field])
    if kind is None or tp.type_ not in TIME_UNITS:
        return Target(True, columns, None, f"unsupported partitioning {tp.type_} on {types[tp.field]}")
    return Target(True, columns, Partitioning(tp.field, kind, tp.type_))


def _delete_prefix(prefix: str) -> None:
    from pyarrow import fs

    with contextlib.suppress(OSError):  # nothing written, or already gone
        fs.GcsFileSystem().delete_dir(prefix.removeprefix("gs://"))


def to_bigquery(
    con: duckdb.DuckDBPyConnection,
    query: str,
    table: str,
    mode: str = "append",
    key: list[str] | tuple[str, ...] = (),
    partition_by: str | None = None,
    cluster_by: list[str] | tuple[str, ...] = (),
    max_bytes_billed: int | None = None,
    staging_uri: str | None = None,
    prune: bool | None = None,
    allow_empty: bool = False,
) -> WriteResult:
    """Writes the result of `query` (DuckDB SQL) to the BigQuery `table` (project.dataset.table).

    to_bigquery(con, "SELECT * FROM accepted", "my-project.finance.journal_lines", mode="replace_partitions")
    to_bigquery(con, "SELECT * FROM scored", "my-project.sales.orders", mode="merge", key=["order_id"])

    `partition_by` (a DATE or TIMESTAMP column, by day) and `cluster_by` apply when append or replace
    creates the table. `max_bytes_billed` caps what the SQL step may bill: BigQuery refuses it beyond.
    `prune` (merge): None reads only the touched partitions when the partition column is in the key;
    True also when it isn't (a row never changes partition: an entry's posting date); False never.
    `allow_empty`: let a `replace` with no rows empty the table (refused by default).
    """
    from google.cloud import bigquery

    from duckless_runtime import log

    seconds: dict[str, float] = {}
    started = time.monotonic()
    table = table_path(table)
    key = tuple(key)
    client = bigquery.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    target = _target(client, table)
    source = tuple(name for name, *_ in con.sql(f"DESCRIBE {query}").fetchall())
    problems = check_request(mode, target, source, key)
    if problems:
        raise ValueError(f"to_bigquery({table}, mode={mode}): " + "; ".join(problems))

    prefix = staging_prefix(os.environ.get("DUCKLESS_JOB_ID"), os.environ.get("DUCKLESS_METRICS_URI"), staging_uri)
    from duckless_runtime import copy_to_parquet

    staging_table = None
    try:
        tick = time.monotonic()
        copy_to_parquet(con, query, prefix)
        seconds["parquet"] = round(time.monotonic() - tick, 2)
        files = f"{prefix}/*.parquet"
        written = con.sql(f"SELECT count(*) FROM read_parquet('{files}')").fetchone()[0]  # footers only
        empty = empty_write_problem(mode, written, allow_empty)
        if empty:
            raise ValueError(f"to_bigquery({table}, mode={mode}): {empty}")

        pruned = mode == "merge" and should_prune(target.partitioning, key, prune)
        needs_partition = target.partitioning is not None and (mode == "replace_partitions" or pruned)
        partition_column = target.partitioning.column if needs_partition else None
        checks = data_checks_sql(files, mode, key, partition_column)
        if checks:
            tick = time.monotonic()
            row = con.sql(checks)
            found = data_problems(dict(zip(row.columns, row.fetchone(), strict=True)))
            seconds["checks"] = round(time.monotonic() - tick, 2)
            if found:
                raise ValueError(f"to_bigquery({table}, mode={mode}): " + "; ".join(found))

        dataset = client.get_dataset(table.rsplit(".", 1)[0])
        load = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            parquet_options=bigquery.ParquetOptions.from_api_repr({"enableListInference": True}),
        )
        if mode in STAGING_MODES:
            # The target's types, so the load converts (or refuses) before the target is touched.
            types = {f.name: f for f in client.get_table(table).schema}
            staging_table = f"{table.rsplit('.', 1)[0]}._duckless_stage_{uuid.uuid4().hex[:12]}"
            stage = bigquery.Table(staging_table, schema=[types[c] for c in source])
            stage.expires = _in(STAGING_TTL_SECONDS)  # gone even if the VM is, mid-write
            client.create_table(stage)
            load.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
            destination = staging_table
        else:
            load.write_disposition = (
                bigquery.WriteDisposition.WRITE_APPEND if mode == "append" else bigquery.WriteDisposition.WRITE_TRUNCATE
            )
            if not target.exists and partition_by:
                load.time_partitioning = bigquery.TimePartitioning(field=partition_by)
            if not target.exists and cluster_by:
                load.clustering_fields = list(cluster_by)
            destination = table

        tick = time.monotonic()
        loaded = client.load_table_from_uri(files, destination, job_config=load, location=dataset.location).result()
        seconds["load"] = round(time.monotonic() - tick, 2)
        rows = int(loaded.output_rows or 0)

        bytes_billed = 0
        if mode in STAGING_MODES:
            target_sql, staging_sql = quote(table), quote(staging_table)
            script = (
                replace_partitions_sql(target_sql, staging_sql, source, target.partitioning)
                if mode == "replace_partitions"
                else merge_sql(
                    target_sql,
                    staging_sql,
                    source,
                    key,
                    target.partitioning,
                    should_prune(target.partitioning, key, prune),
                )
            )
            config = bigquery.QueryJobConfig()
            if max_bytes_billed is not None:
                config.maximum_bytes_billed = max_bytes_billed
            tick = time.monotonic()
            job = client.query(script, job_config=config, location=dataset.location)
            job.result()
            seconds["sql"] = round(time.monotonic() - tick, 2)
            bytes_billed = int(job.total_bytes_billed or 0)
    finally:
        if staging_table:
            client.delete_table(staging_table, not_found_ok=True)
        _delete_prefix(prefix)

    seconds["total"] = round(time.monotonic() - started, 2)
    result = WriteResult(table, mode, rows, bytes_billed, seconds)
    log("bigquery_written", table=table, mode=mode, rows=rows, bytes_billed=bytes_billed, seconds=seconds)
    if mode == "merge" and target.partitioning and not should_prune(target.partitioning, key, prune):
        log(
            "bigquery_merge_not_pruned",
            severity="WARNING",
            table=table,
            hint=f"the MERGE read every partition: add {target.partitioning.column} to the key, or prune=True",
        )
    return result


def _in(seconds: int):
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) + timedelta(seconds=seconds)
