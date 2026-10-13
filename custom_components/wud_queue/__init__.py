"""WUD Queue: What's Up Docker's container updates installed one at a time.

The queue is a sensor. Its state is the queue's (idle, running, stopped, failed), and its attributes hold the items
in order with each one's status, so every dashboard draws the same queue. The actions enqueue, stop, retry and clear
change it. Failures and finished queues are events, for automations to act on.
"""

from __future__ import annotations

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_CLEAR, SERVICE_ENQUEUE, SERVICE_RETRY, SERVICE_STOP
from .queue import Queue

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type WudQueueConfigEntry = ConfigEntry[Queue]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WudQueueConfigEntry) -> bool:
    # The queue isn't restored after a restart: what WUD did meanwhile can't be told, and a wrong guess would run an
    # install twice.
    entry.runtime_data = Queue(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WudQueueConfigEntry) -> bool:
    entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_setup_services(hass: HomeAssistant) -> None:
    def queue() -> Queue:
        entries: list[WudQueueConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        return entries[0].runtime_data

    @callback
    def enqueue(call: ServiceCall) -> None:
        queue().async_enqueue(call.data[ATTR_ENTITY_ID])

    @callback
    def stop(call: ServiceCall) -> None:
        queue().async_stop()

    @callback
    def retry(call: ServiceCall) -> None:
        queue().async_retry()

    @callback
    def clear(call: ServiceCall) -> None:
        queue().async_clear()

    hass.services.async_register(DOMAIN, SERVICE_ENQUEUE, enqueue, schema=vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_ids}))
    hass.services.async_register(DOMAIN, SERVICE_STOP, stop, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_RETRY, retry, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_CLEAR, clear, schema=vol.Schema({}))
