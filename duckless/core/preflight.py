"""Checks a machine choice before submitting: GCE shape rules, then regional quotas."""

from collections.abc import Mapping
from dataclasses import dataclass

from duckless.core.machine import MachineType
from duckless.core.quota import Quota, QuotaCheck, check_quotas, required_quotas


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    region: str
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def quota_check(check: QuotaCheck) -> PreflightCheck:
    available = "unknown in this region" if check.available is None else f"{check.available:g} available"
    return PreflightCheck(name=f"quota {check.metric}", ok=check.ok, detail=f"needs {check.needed:g}, {available}")


def preflight_report(
    region: str, machine: MachineType, local_ssd_count: int, spot: bool, quotas: Mapping[str, Quota]
) -> PreflightReport:
    shape = PreflightCheck("machine", ok=True, detail=f"{machine.name}, {local_ssd_count} local SSD")
    checks = check_quotas(required_quotas(machine, local_ssd_count, spot, quotas), quotas)
    return PreflightReport(region, (shape, *map(quota_check, checks)))


def rejected_machine(region: str, reason: str) -> PreflightReport:
    return PreflightReport(region, (PreflightCheck("machine", ok=False, detail=reason),))
