"""DuckLake catalog of an installation, as the runner sees it (see runtime duckless_runtime.lake)."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LakeConfig:
    instance: str  # Cloud SQL connection name, project:region:instance
    data_path: str  # gcss://<work bucket>/lake/
    service_account: str  # the runner's: its IAM database user


def lake_env(lake: LakeConfig | None) -> Mapping[str, str]:
    """Env the runner needs to start the Cloud SQL Auth Proxy and attach the catalog as `lake`."""
    if lake is None:
        return {}
    return {
        "DUCKLESS_DUCKLAKE_INSTANCE": lake.instance,
        "DUCKLESS_DUCKLAKE_DATA_PATH": lake.data_path,
        "DUCKLESS_DUCKLAKE_USER": lake.service_account,
    }
