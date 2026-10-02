"""Regional quotas a job needs before it can start."""

from collections.abc import Mapping
from dataclasses import dataclass

from duckless.core.machine import LOCAL_SSD_GB, MachineType

PREEMPTIBLE_CPUS = "PREEMPTIBLE_CPUS"
LOCAL_SSD_TOTAL_GB = "LOCAL_SSD_TOTAL_GB"
PREEMPTIBLE_LOCAL_SSD_GB = "PREEMPTIBLE_LOCAL_SSD_GB"
CPUS = "CPUS"


@dataclass(frozen=True, slots=True)
class Quota:
    usage: float
    limit: float

    @property
    def available(self) -> float:
        return self.limit - self.usage


@dataclass(frozen=True, slots=True)
class QuotaCheck:
    metric: str
    needed: float
    available: float | None  # None = metric unknown in the region

    @property
    def ok(self) -> bool:
        return self.available is None or self.available >= self.needed


def required_quotas(
    machine: MachineType, local_ssd_count: int, spot: bool, quotas: Mapping[str, Quota]
) -> dict[str, float]:
    """Quota metric -> amount a single job VM consumes.

    Spot VMs draw on PREEMPTIBLE_* quotas only when the project has one (limit > 0);
    otherwise they fall back on the standard quotas, which is the default for new projects.
    """
    preemptible_cpus = spot and quotas.get(PREEMPTIBLE_CPUS, Quota(0, 0)).limit > 0
    preemptible_ssd = spot and quotas.get(PREEMPTIBLE_LOCAL_SSD_GB, Quota(0, 0)).limit > 0
    cpus = (
        {PREEMPTIBLE_CPUS: machine.vcpus}
        if preemptible_cpus
        else {machine.cpu_quota_metric: machine.vcpus, CPUS: machine.vcpus}
    )
    ssd_metric = PREEMPTIBLE_LOCAL_SSD_GB if preemptible_ssd else LOCAL_SSD_TOTAL_GB
    ssd = {ssd_metric: local_ssd_count * LOCAL_SSD_GB} if local_ssd_count else {}
    return cpus | ssd


def check_quotas(needed: Mapping[str, float], quotas: Mapping[str, Quota]) -> tuple[QuotaCheck, ...]:
    return tuple(
        QuotaCheck(metric=metric, needed=amount, available=quotas[metric].available if metric in quotas else None)
        for metric, amount in sorted(needed.items())
    )
