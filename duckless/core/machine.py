"""Compute Engine machine types and the local SSD rules DuckLess relies on for spill.

GCE only accepts some local SSD counts per machine size, and Batch reports a bad count
~25 s after submission: these rules are checked before anything is sent.
"""

import re
from dataclasses import dataclass

from duckless.core.errors import InvalidLocalSsdCountError, InvalidMachineTypeError

LOCAL_SSD_GB = 375

_MACHINE_RE = re.compile(r"^(?P<family>[a-z][a-z0-9]*)-(?P<kind>[a-z]+)-(?P<vcpus>\d+)(?P<suffix>-lssd)?$")

# Allowed counts of attached local SSDs by vCPU range (0 is always allowed).
# https://cloud.google.com/compute/docs/disks/local-ssd#choose_number_local_ssds
_ATTACHED_LOCAL_SSD_RULES: dict[str, tuple[tuple[range, tuple[int, ...]], ...]] = {
    "n2": (
        (range(2, 11), (1, 2, 4, 8, 16, 24)),
        (range(12, 21), (2, 4, 8, 16, 24)),
        (range(22, 41), (4, 8, 16, 24)),
        (range(42, 81), (8, 16, 24)),
        (range(82, 129), (16, 24)),
    ),
}

# Families whose local SSD comes bundled with the `-lssd` variant, never attached separately.
_BUNDLED_LOCAL_SSD_FAMILIES = frozenset({"c3", "c3d", "c4", "c4a", "c4d"})


@dataclass(frozen=True, slots=True)
class MachineType:
    name: str
    family: str
    kind: str
    vcpus: int
    bundled_local_ssd: bool

    @classmethod
    def parse(cls, name: str) -> "MachineType":
        match = _MACHINE_RE.match(name.strip().lower())
        if not match:
            raise InvalidMachineTypeError(name)
        return cls(
            name=name.strip().lower(),
            family=match["family"],
            kind=match["kind"],
            vcpus=int(match["vcpus"]),
            bundled_local_ssd=match["suffix"] is not None,
        )

    @property
    def cpu_quota_metric(self) -> str:
        """Regional quota metric consumed by this family, e.g. N2_CPUS."""
        return f"{self.family.upper()}_CPUS"


def allowed_local_ssd_counts(machine: MachineType) -> tuple[int, ...] | None:
    """Counts of attachable local SSDs; None when the family's rules are unknown (GCE decides)."""
    if machine.bundled_local_ssd or machine.family in _BUNDLED_LOCAL_SSD_FAMILIES:
        return (0,)
    rules = _ATTACHED_LOCAL_SSD_RULES.get(machine.family)
    if rules is None:
        return None
    return next(((0, *counts) for cpus, counts in rules if machine.vcpus in cpus), (0,))


def validate_local_ssd_count(machine: MachineType, count: int) -> None:
    allowed = allowed_local_ssd_counts(machine)
    if allowed is not None and count not in allowed:
        raise InvalidLocalSsdCountError(machine.name, count, allowed)


def default_local_ssd_count(machine: MachineType) -> int:
    """Smallest non-zero count, so spill lands on SSD by default."""
    allowed = allowed_local_ssd_counts(machine)
    return min((c for c in allowed or (1,) if c > 0), default=0)


def resolve_machine(name: str, local_ssd_count: int | None) -> tuple[MachineType, int]:
    """Parsed machine + SSD count (default: smallest allowed). Raises when GCE would reject them."""
    machine = MachineType.parse(name)
    count = local_ssd_count if local_ssd_count is not None else default_local_ssd_count(machine)
    validate_local_ssd_count(machine, count)
    return machine, count
