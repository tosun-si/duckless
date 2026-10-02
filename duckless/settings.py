"""Deployment settings, read from DUCKLESS_* env vars (direnv) with CLI overrides."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

REQUIRED = {
    "project": "DUCKLESS_PROJECT",
    "bucket": "DUCKLESS_BUCKET",
    "service_account": "DUCKLESS_SA",
    "image": "DUCKLESS_IMAGE",
}


class SettingsError(Exception):
    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(f"missing settings: {', '.join(missing)} (set them in .envrc, see .envrc.example)")


@dataclass(frozen=True, slots=True)
class Settings:
    project: str
    region: str
    bucket: str
    service_account: str
    image: str
    network: str
    subnetwork: str
    external_ip: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str], **overrides: str | None) -> "Settings":
        values = {
            "project": env.get("DUCKLESS_PROJECT", ""),
            "region": env.get("DUCKLESS_REGION", "europe-west1"),
            "bucket": env.get("DUCKLESS_BUCKET", ""),
            "service_account": env.get("DUCKLESS_SA", ""),
            "image": env.get("DUCKLESS_IMAGE", ""),
            "network": env.get("DUCKLESS_NETWORK", "default"),
            "subnetwork": env.get("DUCKLESS_SUBNETWORK", "default"),
        } | {k: v for k, v in overrides.items() if v}
        missing = tuple(var for field, var in REQUIRED.items() if not values[field])
        if missing:
            raise SettingsError(missing)
        settings = cls(**values, external_ip=env.get("DUCKLESS_EXTERNAL_IP", "false").lower() == "true")
        return replace(
            settings,
            network=network_path(settings.project, settings.network),
            subnetwork=subnetwork_path(settings.project, settings.region, settings.subnetwork),
        )


def network_path(project: str, network: str) -> str:
    return network if network.startswith("projects/") else f"projects/{project}/global/networks/{network}"


def subnetwork_path(project: str, region: str, subnetwork: str) -> str:
    return (
        subnetwork
        if subnetwork.startswith("projects/")
        else f"projects/{project}/regions/{region}/subnetworks/{subnetwork}"
    )
