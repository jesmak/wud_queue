"""Shared fixtures: made-up What's Up Docker update entities, a mocked update.install, and the integration set up."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.wud_queue.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Lets Home Assistant load integrations from custom_components/."""


def wud(
    hass: HomeAssistant,
    entity_id: str,
    *,
    installed: str,
    latest: str | None = None,
    stack: str = "",
    service: str = "app",
    image: str = "example/app",
    in_progress: bool = False,
    watcher: str = "local",
) -> None:
    """Writes an update entity shaped like the ones WUD publishes."""
    latest = latest if latest is not None else installed
    attributes: dict[str, Any] = {
        "installed_version": installed,
        "latest_version": latest,
        "in_progress": in_progress,
        "watcher": watcher,
        "stack": stack,
        "name": f"{stack}-{service}-1" if stack else service,
        "display_name": service,
        "image_name": image,
    }
    if stack:
        attributes["labels_com_docker_compose_service"] = service
    hass.states.async_set(entity_id, "on" if latest != installed else "off", attributes)


@pytest.fixture
def installs(hass: HomeAssistant) -> list[ServiceCall]:
    """update.install, mocked: the calls it gets."""
    return async_mock_service(hass, "update", "install")


@pytest.fixture
async def entry(hass: HomeAssistant, installs: list[ServiceCall]) -> AsyncIterator[MockConfigEntry]:
    entry = MockConfigEntry(domain=DOMAIN, title="WUD Queue", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    # A queue left running has timers going.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
