"""Waits for a DuckLess job in the triggerer, so no worker slot is held while it runs."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from airflow.triggers.base import BaseTrigger, TriggerEvent

import duckless


class DuckLessJobTrigger(BaseTrigger):
    """Polls the job's state until it ends; fires one event: the final state, or the error that stopped it."""

    def __init__(self, job_id: str, settings: dict[str, str] | None = None, poll_seconds: float = 30) -> None:
        super().__init__()
        self.job_id = job_id
        self.settings = dict(settings or {})
        self.poll_seconds = poll_seconds

    def serialize(self) -> tuple[str, dict[str, Any]]:
        return (
            "duckless_airflow.triggers.DuckLessJobTrigger",
            {"job_id": self.job_id, "settings": self.settings, "poll_seconds": self.poll_seconds},
        )

    async def run(self) -> AsyncIterator[TriggerEvent]:
        try:
            client = duckless.Client.from_env(**self.settings)
            while True:
                # The client is synchronous: keep it off the triggerer's event loop.
                report = await asyncio.to_thread(client.status, self.job_id)
                if report.status.state.is_terminal:
                    yield TriggerEvent({"job_id": self.job_id, "state": report.status.state.value})
                    return
                await asyncio.sleep(self.poll_seconds)
        except Exception as e:
            yield TriggerEvent({"job_id": self.job_id, "state": "ERROR", "error": f"{type(e).__name__}: {e}"})
