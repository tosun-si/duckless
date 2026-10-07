"""Where a job runs: Cloud Batch (any machine, local SSD, Spot) or Cloud Run Jobs (starts in seconds)."""

from dataclasses import dataclass
from enum import StrEnum

from duckless.core.errors import InvalidJobError
from duckless.core.machine import MachineType, memory_gb

# Cloud Run Jobs limits: CPU in fixed steps, memory up to 32 GiB, more memory needs more CPU.
CLOUD_RUN_CPUS = (1, 2, 4, 6, 8)
CLOUD_RUN_MAX_MEMORY_GIB = 32
_MIN_CPU_FOR_MEMORY_GIB = ((24, 8), (16, 6), (8, 4), (4, 2), (0, 1))


class ExecutorKind(StrEnum):
    BATCH = "batch"
    CLOUD_RUN = "cloudrun"


class Placement(StrEnum):
    AUTO = "auto"
    BATCH = "batch"
    CLOUD_RUN = "cloudrun"


@dataclass(frozen=True, slots=True)
class CloudRunShape:
    cpu: int
    memory_gib: int

    @property
    def limits(self) -> dict[str, str]:
        return {"cpu": str(self.cpu), "memory": f"{self.memory_gib}Gi"}


def cloud_run_shape(machine: MachineType) -> CloudRunShape | None:
    """Smallest Cloud Run size giving the machine's vCPUs and memory; None when it doesn't fit."""
    memory = memory_gb(machine)
    if memory is None or memory > CLOUD_RUN_MAX_MEMORY_GIB:
        return None
    memory_gib = max(1, int(memory))
    min_cpu = next(cpu for threshold, cpu in _MIN_CPU_FOR_MEMORY_GIB if memory_gib > threshold)
    cpu = next((c for c in CLOUD_RUN_CPUS if c >= max(machine.vcpus, min_cpu)), None)
    return CloudRunShape(cpu, memory_gib) if cpu is not None else None


def choose_executor(
    placement: Placement, machine: MachineType, spot: bool, local_ssd_count: int | None
) -> ExecutorKind:
    """`auto` takes Cloud Run only when it changes nothing for the job: same size, no Spot, no local SSD asked."""
    fits = cloud_run_shape(machine) is not None
    if placement is Placement.BATCH:
        return ExecutorKind.BATCH
    if placement is Placement.CLOUD_RUN:
        if not fits:
            raise InvalidJobError(
                f"{machine.name} does not fit Cloud Run Jobs (at most 8 vCPU and 32 GiB); use --on batch"
            )
        if spot or local_ssd_count:
            raise InvalidJobError("Cloud Run Jobs has neither Spot nor local SSD; drop --spot / --local-ssd")
        return ExecutorKind.CLOUD_RUN
    return ExecutorKind.CLOUD_RUN if fits and not spot and not local_ssd_count else ExecutorKind.BATCH
