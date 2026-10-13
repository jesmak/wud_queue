"""The queue's sensor: its state, and its items in the attributes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WudQueueConfigEntry
from .const import DOMAIN, STATES
from .queue import Queue


async def async_setup_entry(hass: HomeAssistant, entry: WudQueueConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([QueueSensor(entry)])


class QueueSensor(SensorEntity):
    _attr_has_entity_name = True
    # The entity is the device's only one and takes its name, so it is sensor.wud_queue.
    _attr_name = None
    _attr_translation_key = "queue"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES
    _attr_should_poll = False
    # The items change with every step of a run, and the recorder has no use for them.
    _unrecorded_attributes = frozenset({"items", "stopping"})

    def __init__(self, entry: WudQueueConfigEntry) -> None:
        self._queue: Queue = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_queue"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="WUD Queue",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._queue.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self._queue.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._queue.attributes()
