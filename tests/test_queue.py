"""The queue: one install at a time, failures worked out from WUD's states, stop, retry and the events."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.wud_queue.const import (
    DOMAIN,
    EVENT_FAILED,
    EVENT_FINISHED,
    KEEP_FINISHED,
    RUN_TIMEOUT,
    START_TIMEOUT,
    VERSION_GRACE,
)

from .conftest import wud

SENSOR = "sensor.wud_queue"


async def enqueue(hass: HomeAssistant, *entity_ids: str) -> None:
    await hass.services.async_call(DOMAIN, "enqueue", {"entity_id": list(entity_ids)}, blocking=True)
    await hass.async_block_till_done()


def installed(calls: list[ServiceCall]) -> list[str]:
    """The entities update.install was called for, in order."""
    return [call.data["entity_id"] for call in calls]


def items(hass: HomeAssistant) -> list[tuple[str, str]]:
    state = hass.states.get(SENSOR)
    assert state is not None
    return [(item["name"], item["status"]) for item in state.attributes["items"]]


async def install_works(hass: HomeAssistant, entity_id: str, **attributes: Any) -> None:
    """WUD installing an update the way it does: marked as installing, then on the new version."""
    wud(hass, entity_id, **attributes, in_progress=True)
    await hass.async_block_till_done()
    wud(hass, entity_id, **{**attributes, "installed": attributes["latest"]})
    await hass.async_block_till_done()


async def wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_installs_one_at_a_time(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", stack="app", service="db", image="library/postgres")
    finished = async_capture_events(hass, EVENT_FINISHED)

    await enqueue(hass, "update.proxy", "update.db")
    assert installed(installs) == ["update.proxy"]
    assert hass.states.get(SENSOR).state == "running"
    assert items(hass) == [("proxy", "running"), ("db", "queued")]

    await install_works(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    assert installed(installs) == ["update.proxy", "update.db"]
    await install_works(hass, "update.db", installed="16.4", latest="16.5", stack="app", service="db", image="library/postgres")

    assert hass.states.get(SENSOR).state == "idle"
    assert items(hass) == [("proxy", "done"), ("db", "done")]
    assert [(e.data["done"], e.data["failed"], e.data["not_run"]) for e in finished] == [(2, 0, 0)]


async def test_installs_services_of_one_image_as_one_item(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    for service in ("api", "worker"):
        wud(hass, f"update.shop_{service}", installed="24.1", latest="24.2", stack="shop", service=service, image="library/node")

    await enqueue(hass, "update.shop_api", "update.shop_worker")
    assert items(hass) == [("node", "running")]
    assert installed(installs) == ["update.shop_api"]
    await install_works(hass, "update.shop_api", installed="24.1", latest="24.2", stack="shop", service="api", image="library/node")
    assert installed(installs) == ["update.shop_api", "update.shop_worker"]


async def test_puts_wud_last(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    wud(hass, "update.wud", installed="9.4.1", latest="9.5.0", stack="wud", service="wud", image="getwud/wud")
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")

    await enqueue(hass, "update.wud", "update.proxy")
    await enqueue(hass, "update.db")
    assert items(hass) == [("proxy", "running"), ("db", "queued"), ("wud", "queued")]


async def test_wud_replacing_itself_counts_once_its_version_shows(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]
) -> None:
    wud(hass, "update.wud", installed="9.4.1", latest="9.5.0", stack="wud", service="wud", image="getwud/wud")
    await enqueue(hass, "update.wud")
    wud(hass, "update.wud", installed="9.4.1", latest="9.5.0", stack="wud", service="wud", image="getwud/wud", in_progress=True)
    await hass.async_block_till_done()
    # Gone while WUD restarts, then back on the new version with in_progress left on.
    hass.states.async_set("update.wud", "unavailable", {})
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "running"
    wud(hass, "update.wud", installed="9.5.0", stack="wud", service="wud", image="getwud/wud", in_progress=True)
    await hass.async_block_till_done()
    assert items(hass) == [("wud", "done")]


async def test_fails_when_wud_never_starts(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")
    failed: list[Event] = async_capture_events(hass, EVENT_FAILED)

    await enqueue(hass, "update.proxy", "update.db")
    await wait(hass, freezer, START_TIMEOUT - 5)
    assert hass.states.get(SENSOR).state == "running"
    await wait(hass, freezer, 10)

    assert hass.states.get(SENSOR).state == "failed"
    assert items(hass) == [("proxy", "failed"), ("db", "not_run")]
    assert installed(installs) == ["update.proxy"]
    assert [(e.data["name"], e.data["reason"], e.data["installed_version"], e.data["target_version"]) for e in failed] == [
        ("proxy", "start", "2.10", "2.11")
    ]


async def test_fails_when_the_version_stays(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    attributes: dict[str, Any] = {"installed": "2.10", "latest": "2.11", "service": "proxy", "image": "library/caddy"}
    wud(hass, "update.proxy", **attributes)
    await enqueue(hass, "update.proxy")
    wud(hass, "update.proxy", **attributes, in_progress=True)
    await hass.async_block_till_done()
    wud(hass, "update.proxy", **attributes)
    await hass.async_block_till_done()

    await wait(hass, freezer, VERSION_GRACE - 5)
    assert hass.states.get(SENSOR).state == "running"
    await wait(hass, freezer, 10)
    assert hass.states.get(SENSOR).state == "failed"
    assert hass.states.get(SENSOR).attributes["items"][0]["reason"] == "version"


async def test_fails_an_install_that_never_ends(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    await enqueue(hass, "update.proxy")
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy", in_progress=True)
    await hass.async_block_till_done()
    for _ in range(RUN_TIMEOUT // 600):
        await wait(hass, freezer, 600)
    await wait(hass, freezer, 10)
    assert hass.states.get(SENSOR).attributes["items"][0]["reason"] == "timeout"


async def test_says_why_when_home_assistant_refuses(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    async def refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("No update available")

    hass.services.async_register("update", "install", refuse)
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    await enqueue(hass, "update.proxy")

    item = hass.states.get(SENSOR).attributes["items"][0]
    assert (item["status"], item["reason"], item["error"]) == ("failed", "service", "No update available")


async def test_stops_after_the_whole_item(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    for service in ("api", "worker"):
        wud(hass, f"update.shop_{service}", installed="24.1", latest="24.2", stack="shop", service=service, image="library/node")
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    finished = async_capture_events(hass, EVENT_FINISHED)

    await enqueue(hass, "update.shop_api", "update.shop_worker", "update.proxy")
    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    assert hass.states.get(SENSOR).attributes["stopping"] is True

    await install_works(hass, "update.shop_api", installed="24.1", latest="24.2", stack="shop", service="api", image="library/node")
    # The other service of the same compose project still goes, so its compose file isn't left half updated.
    assert installed(installs) == ["update.shop_api", "update.shop_worker"]
    await install_works(hass, "update.shop_worker", installed="24.1", latest="24.2", stack="shop", service="worker", image="library/node")

    assert hass.states.get(SENSOR).state == "stopped"
    assert items(hass) == [("node", "done"), ("proxy", "not_run")]
    assert [(e.data["done"], e.data["not_run"]) for e in finished] == [(1, 1)]


async def test_retries_what_failed_and_what_did_not_run(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")
    await enqueue(hass, "update.proxy", "update.db")
    await wait(hass, freezer, START_TIMEOUT + 10)
    assert hass.states.get(SENSOR).state == "failed"

    await hass.services.async_call(DOMAIN, "retry", {}, blocking=True)
    await hass.async_block_till_done()
    assert items(hass) == [("proxy", "running"), ("db", "queued")]
    assert installed(installs) == ["update.proxy", "update.proxy"]


async def test_adds_to_a_running_queue_without_doubles(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")
    await enqueue(hass, "update.proxy")
    await enqueue(hass, "update.proxy", "update.db")
    assert items(hass) == [("proxy", "running"), ("db", "queued")]


async def test_refuses_what_it_cannot_queue(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.states.async_set("update.home_assistant_core", "on", {"installed_version": "2026.10.0"})
    with pytest.raises(ServiceValidationError, match="not an update entity"):
        await enqueue(hass, "update.home_assistant_core")

    wud(hass, "update.proxy", installed="2.11", service="proxy", image="library/caddy")
    with pytest.raises(ServiceValidationError, match="None of the entities"):
        await enqueue(hass, "update.proxy")

    with pytest.raises(ServiceValidationError, match="nothing to retry"):
        await hass.services.async_call(DOMAIN, "retry", {}, blocking=True)


async def test_clears_a_finished_queue_but_keeps_a_failed_one(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    attributes: dict[str, Any] = {"installed": "2.10", "latest": "2.11", "service": "proxy", "image": "library/caddy"}
    wud(hass, "update.proxy", **attributes)
    await enqueue(hass, "update.proxy")
    await install_works(hass, "update.proxy", **attributes)
    assert items(hass) == [("proxy", "done")]
    await wait(hass, freezer, KEEP_FINISHED + 10)
    assert items(hass) == []

    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")
    await enqueue(hass, "update.db")
    await wait(hass, freezer, START_TIMEOUT + 10)
    await wait(hass, freezer, KEEP_FINISHED + 10)
    assert items(hass) == [("db", "failed")]


async def test_clears_on_request_but_not_while_running(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    attributes: dict[str, Any] = {"installed": "2.10", "latest": "2.11", "service": "proxy", "image": "library/caddy"}
    wud(hass, "update.proxy", **attributes)
    await enqueue(hass, "update.proxy")
    with pytest.raises(ServiceValidationError, match="is running"):
        await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)

    await install_works(hass, "update.proxy", **attributes)
    await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)
    await hass.async_block_till_done()
    assert (hass.states.get(SENSOR).state, items(hass)) == ("idle", [])


async def test_marks_the_containers_of_what_did_not_run(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")
    await enqueue(hass, "update.proxy", "update.db")
    await wait(hass, freezer, START_TIMEOUT + 10)
    db = hass.states.get(SENSOR).attributes["items"][1]
    assert (db["status"], [e["status"] for e in db["entities"]]) == ("not_run", ["not_run"])


async def test_installs_a_version_newer_than_when_queued(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    db: dict[str, Any] = {"installed": "16.4", "latest": "16.5", "service": "db", "image": "library/postgres"}
    wud(hass, "update.db", **db)
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    await enqueue(hass, "update.db", "update.proxy")

    # A newer proxy comes out while the database installs, and the install gets that one.
    wud(hass, "update.proxy", installed="2.10", latest="2.12", service="proxy", image="library/caddy")
    await install_works(hass, "update.db", **db)
    assert installed(installs) == ["update.db", "update.proxy"]
    await install_works(hass, "update.proxy", installed="2.10", latest="2.12", service="proxy", image="library/caddy")

    assert hass.states.get(SENSOR).state == "idle"
    assert hass.states.get(SENSOR).attributes["items"][1]["entities"][0]["to"] == "2.12"


async def test_joins_a_service_to_its_group_while_it_installs(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]
) -> None:
    for service in ("api", "worker"):
        wud(hass, f"update.shop_{service}", installed="24.1", latest="24.2", stack="shop", service=service, image="library/node")
    await enqueue(hass, "update.shop_api")
    await enqueue(hass, "update.shop_worker")
    assert items(hass) == [("node", "running")]

    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    await install_works(hass, "update.shop_api", installed="24.1", latest="24.2", stack="shop", service="api", image="library/node")
    # The stop waits for the whole group, the service queued later included.
    assert installed(installs) == ["update.shop_api", "update.shop_worker"]


async def test_refuses_to_queue_while_stopping(hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall]) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    wud(hass, "update.db", installed="16.4", latest="16.5", service="db", image="library/postgres")
    await enqueue(hass, "update.proxy")
    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    with pytest.raises(ServiceValidationError, match="stopping"):
        await enqueue(hass, "update.db")


async def test_says_why_whatever_refuses_the_install(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    async def refuse(call: ServiceCall) -> None:
        raise ValueError("Unexpected answer")

    hass.services.async_register("update", "install", refuse)
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    await enqueue(hass, "update.proxy")
    item = hass.states.get(SENSOR).attributes["items"][0]
    assert (item["status"], item["reason"], item["error"]) == ("failed", "service", "Unexpected answer")


async def test_unloading_mid_run_leaves_nothing_behind(
    hass: HomeAssistant, entry: MockConfigEntry, installs: list[ServiceCall], freezer: FrozenDateTimeFactory
) -> None:
    wud(hass, "update.proxy", installed="2.10", latest="2.11", service="proxy", image="library/caddy")
    finished = async_capture_events(hass, EVENT_FINISHED)
    await enqueue(hass, "update.proxy")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # Without the entry, the time that would have failed the install passes without an event.
    await wait(hass, freezer, START_TIMEOUT + 10)
    assert finished == []
