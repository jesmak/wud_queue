"""The queue: What's Up Docker's installs one at a time, and how each one went.

An item is what the card shows as one row: a container, or the services of one compose project that run the same
image and are headed for the same version. An item's containers are installed one after another, and a stop takes
effect only after the whole item, so that a compose file is never left with some of its services updated.

WUD reports no failures. It marks a container as installing (`in_progress`) when the install command arrives and
unmarks it when the install ends, whether it worked or not, so a failure shows only as the old version staying. An
install is done once the container is on its new version, whatever `in_progress` says: after WUD has replaced
itself, its own entity keeps `in_progress` on for good.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    AVAILABLE_TIMEOUT,
    DOMAIN,
    DONE,
    EVENT_FAILED,
    EVENT_FINISHED,
    FAILED,
    IDLE,
    KEEP_FINISHED,
    NOT_RUN,
    QUEUED,
    REASON_SERVICE,
    REASON_START,
    REASON_TIMEOUT,
    REASON_VERSION,
    RUN_TIMEOUT,
    RUNNING,
    SELF_UPDATE_HELPER,
    START_TIMEOUT,
    STOPPED,
    TICK,
    VERSION_GRACE,
    WUD_IMAGE,
)

_LOGGER = logging.getLogger(__name__)


def is_wud(state: State | None) -> bool:
    """An update entity WUD made: one with a container's watcher and image in it."""
    return (
        state is not None
        and state.domain == "update"
        and isinstance(state.attributes.get("watcher"), str)
        and isinstance(state.attributes.get("image_name"), str)
    )


def _installable(state: State) -> bool:
    """A WUD entity with an update to install now: not WUD's leftover self-update helper, and not installing already."""
    return (
        state.state == STATE_ON
        and not SELF_UPDATE_HELPER.search(_text(state.attributes.get("name")))
        and state.attributes.get("in_progress") is not True
    )


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass
class Target:
    """One container of an item, and how its install is going."""

    entity_id: str
    installed: str
    target: str
    status: str = QUEUED
    # When the install was asked for, when WUD was seen installing, and when it was seen finishing.
    asked: float | None = None
    started: bool = False
    ended: float | None = None
    # Since when its turn has waited for its entity to be available.
    waiting: float | None = None


@dataclass
class Item:
    key: str
    name: str
    wud: bool
    targets: list[Target]
    status: str = QUEUED
    reason: str | None = None
    # Home Assistant's error, when it refused to send the install.
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "error": self.error,
            "entities": [{"entity_id": t.entity_id, "from": t.installed, "to": t.target, "status": t.status} for t in self.targets],
        }


@dataclass
class _Container:
    entity_id: str
    key: str
    name: str
    image: str
    installed: str
    latest: str


def _container(state: State) -> _Container:
    a = state.attributes
    image = _text(a.get("image_name")) or state.entity_id
    latest = _text(a.get("latest_version"))
    project = _text(a.get("stack")) or _text(a.get("labels_com_docker_compose_project")) or f":{state.entity_id}"
    return _Container(
        entity_id=state.entity_id,
        # The same key as the card's rows: one per watcher (Docker host), project, image and version headed for.
        key=f"{_text(a.get('watcher'))}|{project}/{image}@{latest}",
        name=_text(a.get("labels_wud_display_name"))
        or _text(a.get("labels_com_docker_compose_service"))
        or _text(a.get("display_name"))
        or _text(a.get("name"))
        or state.entity_id,
        image=image,
        installed=_text(a.get("installed_version")),
        latest=latest,
    )


@dataclass
class Queue:
    hass: HomeAssistant
    entry: ConfigEntry
    items: list[Item] = field(default_factory=list)
    state: str = IDLE
    stopping: bool = False
    _listeners: list[Callable[[], None]] = field(default_factory=list)
    _unsub_tick: CALLBACK_TYPE | None = None
    _unsub_states: CALLBACK_TYPE | None = None
    _unsub_clear: CALLBACK_TYPE | None = None
    # Set when the entry is unloaded, so that an install answering late changes nothing.
    _closed: bool = False

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def attributes(self) -> dict[str, Any]:
        return {"items": [item.as_dict() for item in self.items], "stopping": self.stopping}

    @callback
    def async_enqueue(self, entity_ids: list[str]) -> None:
        """Queues the updates of these entities, and starts the queue if it isn't running."""
        if self.stopping:
            # A stop asked for is kept: the rest of the queue is about to be dropped, and so would this be.
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="stopping")
        queued = {t.entity_id for item in self.items if item.status in (QUEUED, RUNNING) for t in item.targets}
        new: list[_Container] = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if not is_wud(state):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="not_wud", translation_placeholders={"entity_id": entity_id}
                )
            assert state is not None
            container = _container(state)
            if _installable(state) and container.installed != container.latest and entity_id not in queued:
                new.append(container)
                queued.add(entity_id)
        if not new:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nothing_to_install")

        if self.state != RUNNING:
            # A finished, stopped or failed queue makes way for the new one.
            self._cancel_clear()
            self.items = []
        for container in new:
            target = Target(container.entity_id, container.installed, container.latest)
            # A service joins its group's item even while it installs, as the item's containers go one at a time.
            waiting = next((i for i in self.items if i.key == container.key and i.status in (QUEUED, RUNNING)), None)
            if waiting:
                waiting.targets.append(target)
                waiting.name = container.image.split("/")[-1]
            else:
                self.items.append(
                    Item(key=container.key, name=container.name, wud=bool(WUD_IMAGE.search(container.image)), targets=[target])
                )
        self._wud_last()
        self._start()

    @callback
    def async_stop(self) -> None:
        """Lets the current item finish, and drops the rest."""
        if self.state == RUNNING:
            self.stopping = True
            self._step()

    @callback
    def async_retry(self) -> None:
        """Queues a failed or stopped queue's unfinished items again, and starts it."""
        if self.state not in (FAILED, STOPPED):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nothing_to_retry")
        items: list[Item] = []
        for item in self.items:
            if item.status == DONE:
                continue
            # A container that has reached its version meanwhile, or no longer has the update, is left out.
            targets = []
            for t in item.targets:
                state = self.hass.states.get(t.entity_id)
                if state is not None and _installable(state):
                    # The update may be a newer one by now, and that is what an install gets.
                    container = _container(state)
                    targets.append(Target(t.entity_id, container.installed, container.latest))
            if targets:
                items.append(Item(key=item.key, name=item.name, wud=item.wud, targets=targets))
        if not items:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nothing_to_retry")
        self._cancel_clear()
        self.items = items
        self._wud_last()
        self._start()

    @callback
    def async_clear(self) -> None:
        """Empties a queue that isn't running, as it would be ten minutes after it finished."""
        if self.state == RUNNING:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="running")
        self._cancel_clear()
        self._clear(None)

    @callback
    def async_shutdown(self) -> None:
        """Lets go of everything at unload. An install already sent is left to WUD."""
        self._closed = True
        self._stop_watching()
        self._cancel_clear()

    def _wud_last(self) -> None:
        """WUD's own container goes last of the items still waiting, as installing it restarts WUD."""
        waiting = [i for i in self.items if i.status == QUEUED]
        rest = [i for i in self.items if i.status != QUEUED]
        self.items = rest + sorted(waiting, key=lambda i: i.wud)

    def _start(self) -> None:
        if self.state != RUNNING:
            self.state = RUNNING
            self.stopping = False
            self._unsub_tick = async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK))
        self._watch()
        self._step()

    def _watch(self) -> None:
        """Follows the update entities of every item still to run, so a change is acted on at once."""
        if self._unsub_states:
            self._unsub_states()
        entity_ids = [t.entity_id for item in self.items if item.status in (QUEUED, RUNNING) for t in item.targets]

        @callback
        def changed(_: Event[EventStateChangedData]) -> None:
            self._step()

        self._unsub_states = async_track_state_change_event(self.hass, entity_ids, changed)

    def _stop_watching(self) -> None:
        for unsub in (self._unsub_tick, self._unsub_states):
            if unsub:
                unsub()
        self._unsub_tick = self._unsub_states = None

    def _cancel_clear(self) -> None:
        if self._unsub_clear:
            self._unsub_clear()
            self._unsub_clear = None

    @callback
    def _tick(self, _: Any) -> None:
        self._step()

    @callback
    def _step(self) -> None:
        """Moves the queue on: follows the running item, and starts the next one when it is done."""
        if self.state != RUNNING or self._closed:
            return
        now = dt_util.utcnow().timestamp()
        while True:
            item = next((i for i in self.items if i.status in (QUEUED, RUNNING)), None)
            if item is None:
                # Everything ran, so a stop asked for during the last item left nothing out.
                self._finish(IDLE)
                return
            if item.status == QUEUED:
                if self.stopping:
                    self._finish(STOPPED)
                    return
                item.status = RUNNING
            status = self._follow(item, now)
            if status == RUNNING:
                break
            item.status = status
            if status == FAILED:
                self._fail(item)
                return
        self._changed()

    def _follow(self, item: Item, now: float) -> str:
        """How the item's install is going: still running, done, or failed with its reason set."""
        for t in item.targets:
            if t.status == DONE:
                continue
            state = self.hass.states.get(t.entity_id)
            if state is not None and state.attributes.get("installed_version") == t.target:
                t.status = DONE
                continue
            if t.status == QUEUED:
                if not is_wud(state):
                    # Home Assistant would drop the install without a word, so it waits for the entity to come back.
                    t.waiting = t.waiting if t.waiting is not None else now
                    if now - t.waiting > AVAILABLE_TIMEOUT:
                        return self._failed(item, t, REASON_START)
                    return RUNNING
                assert state is not None
                # update.install gets whatever version is newest at the time, which may be newer than when queued.
                container = _container(state)
                t.installed, t.target = container.installed, container.latest
                if t.installed == t.target:
                    t.status = DONE
                    continue
                t.status = RUNNING
                t.asked = now
                self._install(item, t)
                return RUNNING
            assert t.asked is not None
            if item.error is not None:
                return self._failed(item, t, REASON_SERVICE)
            if now - t.asked > RUN_TIMEOUT:
                return self._failed(item, t, REASON_TIMEOUT)
            if not is_wud(state):
                # Unavailable, such as while WUD restarts after updating itself. It comes back.
                return RUNNING
            assert state is not None
            # Home Assistant turns a percentage into true, and gives the number as update_percentage.
            if state.attributes.get("in_progress") is True:
                t.started = True
                t.ended = None
            elif t.started and t.ended is None:
                t.ended = now
            if not t.started and now - t.asked > START_TIMEOUT:
                return self._failed(item, t, REASON_START)
            if t.ended is not None and now - t.ended > VERSION_GRACE:
                return self._failed(item, t, REASON_VERSION)
            return RUNNING
        return DONE

    @staticmethod
    def _failed(item: Item, target: Target, reason: str) -> str:
        target.status = FAILED
        item.reason = reason
        return FAILED

    def _install(self, item: Item, target: Target) -> None:
        async def install() -> None:
            try:
                await self.hass.services.async_call("update", "install", {"entity_id": target.entity_id}, blocking=True)
            except Exception as err:  # Whatever refused it, the row says so instead of waiting for WUD.
                _LOGGER.warning("Home Assistant did not send the install of %s: %s", target.entity_id, err)
                item.error = str(err) or type(err).__name__
                self._step()

        # The entry's task, so that it is cancelled when the entry is unloaded.
        self.entry.async_create_background_task(self.hass, install(), f"{DOMAIN} install {target.entity_id}", eager_start=False)

    def _fail(self, item: Item) -> None:
        failed = next(t for t in item.targets if t.status == FAILED)
        state = self.hass.states.get(failed.entity_id)
        self.hass.bus.async_fire(
            EVENT_FAILED,
            {
                "name": item.name,
                "entity_ids": [t.entity_id for t in item.targets],
                "reason": item.reason,
                "error": item.error,
                "installed_version": state.attributes.get("installed_version") if state else None,
                "target_version": failed.target,
            },
        )
        self._finish(FAILED)

    def _finish(self, state: str) -> None:
        for item in self.items:
            if item.status == QUEUED:
                item.status = NOT_RUN
                for target in item.targets:
                    target.status = NOT_RUN
        self.state = state
        self.stopping = False
        self._stop_watching()
        counts = {status: sum(1 for i in self.items if i.status == status) for status in (DONE, FAILED, NOT_RUN)}
        self.hass.bus.async_fire(EVENT_FINISHED, {**counts, "items": [item.as_dict() for item in self.items]})
        if state != FAILED:
            self._unsub_clear = async_call_later(self.hass, KEEP_FINISHED, self._clear)
        self._changed()

    @callback
    def _clear(self, _: Any) -> None:
        self._unsub_clear = None
        if self.state != RUNNING:
            self.items = []
            self.state = IDLE
            self._changed()

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()
