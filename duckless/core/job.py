"""A DuckLess job: what is asked, what is submitted, where it stands."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from duckless.core.errors import InvalidJobError
from duckless.core.machine import MachineType, resolve_machine, validate_local_ssd_count

MAX_JOB_ID_LENGTH = 63
SOURCE_KINDS = {".sql": "sql", ".py": "py"}


class JobKind(StrEnum):
    SQL = "sql"  # a .sql file run by the default runner
    PYTHON = "py"  # a .py file run by the default runner, duckless_runtime.connect() available
    COMMAND = "command"  # any image + command (dbt build, …)


class JobState(StrEnum):
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        return self in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


@dataclass(frozen=True, slots=True)
class JobRequest:
    """What the user asks: a local .sql / .py file for the default runner, or an image + command."""

    machine: str
    source: Path | None = None
    image: str | None = None
    command: tuple[str, ...] = ()
    spot: bool = False
    local_ssd_count: int | None = None  # None = smallest count the machine accepts
    env: Mapping[str, str] = field(default_factory=dict)
    max_run_seconds: int = 3 * 3600
    name: str | None = None


@dataclass(frozen=True, slots=True)
class JobSpec:
    """What is submitted: fully resolved and validated."""

    job_id: str
    kind: JobKind
    image: str
    machine: MachineType
    spot: bool
    local_ssd_count: int
    max_run_seconds: int
    source_uri: str | None = None
    command: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not job_id_is_valid(self.job_id):
            raise InvalidJobError(f"invalid job id '{self.job_id}'")
        if self.kind is JobKind.COMMAND and not self.command:
            raise InvalidJobError("a command job needs a command")
        if self.kind is not JobKind.COMMAND and not self.source_uri:
            raise InvalidJobError(f"a {self.kind} job needs a source file")
        if self.max_run_seconds <= 0:
            raise InvalidJobError("max_run_seconds must be positive")
        validate_local_ssd_count(self.machine, self.local_ssd_count)

    @property
    def runner_args(self) -> tuple[str, ...]:
        """Container args: the runner's `sql|py <uri>` or the user's own command."""
        return self.command if self.kind is JobKind.COMMAND else (self.kind.value, self.source_uri or "")


@dataclass(frozen=True, slots=True)
class JobEvent:
    at: datetime
    description: str


@dataclass(frozen=True, slots=True)
class JobStatus:
    job_id: str
    uid: str
    state: JobState
    machine: str
    spot: bool
    created_at: datetime
    run_seconds: float | None = None
    events: tuple[JobEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class JobReport:
    """Status, plus the runner's metrics once the job is over."""

    status: JobStatus
    metrics: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LogLine:
    at: datetime
    severity: str
    message: str
    fields: Mapping[str, Any] = field(default_factory=dict)


# ---------- rules ----------


def job_id_is_valid(job_id: str) -> bool:
    """Batch job ids: ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$"""
    return (
        0 < len(job_id) <= MAX_JOB_ID_LENGTH
        and job_id[0].isalpha()
        and job_id[-1].isalnum()
        and all(c.islower() or c.isdigit() or c == "-" for c in job_id)
    )


def new_job_id(name: str, now: datetime, nonce: str) -> str:
    """dl-<name>-<yyyymmdd-hhmmss>-<nonce4>, always a valid Batch id."""
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    suffix = f"-{now:%Y%m%d-%H%M%S}-{nonce[:4].lower()}"
    head = f"dl-{slug}"[: MAX_JOB_ID_LENGTH - len(suffix)].rstrip("-")
    return f"{head}{suffix}"


def job_kind(request: JobRequest) -> JobKind:
    if request.command and request.source:
        raise InvalidJobError("give either a source file or a command, not both")
    if request.command:
        return JobKind.COMMAND
    if request.source is None:
        raise InvalidJobError("nothing to run: give a .sql / .py file or a command")
    kind = SOURCE_KINDS.get(request.source.suffix.lower())
    if kind is None:
        raise InvalidJobError(f"unsupported source '{request.source.name}' (expected .sql or .py)")
    return JobKind(kind)


def job_name(request: JobRequest) -> str:
    return request.name or (request.source.stem if request.source else request.command[0].rsplit("/", 1)[-1])


def merged_env(user_env: Mapping[str, str], runner_env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Runner keys win: user code must not redirect metrics or the work bucket by accident."""
    return tuple(sorted({**user_env, **runner_env}.items()))


def plan_job(request: JobRequest, *, default_image: str, now: datetime, nonce: str) -> JobSpec:
    """Validated spec, before the source is uploaded (its URI is a placeholder until then)."""
    kind = job_kind(request)
    machine, local_ssd_count = resolve_machine(request.machine, request.local_ssd_count)
    return JobSpec(
        job_id=new_job_id(job_name(request), now, nonce),
        kind=kind,
        image=request.image or default_image,
        machine=machine,
        spot=request.spot,
        local_ssd_count=local_ssd_count,
        max_run_seconds=request.max_run_seconds,
        source_uri="pending-upload" if kind is not JobKind.COMMAND else None,
        command=request.command,
    )
