"""Triggers Valkey plan upgrades via Aiven API."""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Literal

import aiohttp


@dataclass
class ServiceState:
    """Current state of an Aiven service."""

    service_name: str
    state: str  # RUNNING, REBUILDING, REBALANCING, etc.
    plan: str

    @property
    def is_ready(self) -> bool:
        """Check if service is in RUNNING state."""
        return self.state == "RUNNING"


class AivenClient:
    """Async client for Aiven API."""

    BASE_URL = "https://api.aiven.io/v1"

    def __init__(self, token: str | None = None):
        """Initialize client with API token."""
        self._token = token or os.environ.get("AIVEN_TOKEN")
        if not self._token:
            raise ValueError("AIVEN_TOKEN environment variable not set")
        self._headers = {"Authorization": f"aivenv1 {self._token}"}

    async def get_service(self, project: str, service_name: str) -> ServiceState:
        """Get current service state."""
        url = f"{self.BASE_URL}/project/{project}/service/{service_name}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                service = data["service"]

                return ServiceState(
                    service_name=service["service_name"],
                    state=service["state"],
                    plan=service["plan"],
                )

    async def upgrade_plan(
        self, project: str, service_name: str, target_plan: str
    ) -> ServiceState:
        """Trigger a plan upgrade."""
        url = f"{self.BASE_URL}/project/{project}/service/{service_name}"
        payload = {"plan": target_plan}

        async with aiohttp.ClientSession() as session:
            async with session.put(
                url, headers=self._headers, json=payload
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                service = data["service"]

                return ServiceState(
                    service_name=service["service_name"],
                    state=service["state"],
                    plan=service["plan"],
                )


async def wait_for_running(
    client: AivenClient,
    project: str,
    service_name: str,
    poll_interval_sec: int = 5,
    timeout_sec: int = 3600,
    on_state_change: callable = None,
) -> ServiceState:
    """Wait for service to reach RUNNING state."""
    start_time = time.time()
    last_state = None

    while time.time() - start_time < timeout_sec:
        state = await client.get_service(project, service_name)

        if state.state != last_state:
            if on_state_change:
                on_state_change(state)
            last_state = state.state

        if state.is_ready:
            return state

        await asyncio.sleep(poll_interval_sec)

    raise TimeoutError(
        f"Service {service_name} did not reach RUNNING state within {timeout_sec}s"
    )
