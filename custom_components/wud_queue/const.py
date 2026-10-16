"""Constants for WUD Queue."""

from __future__ import annotations

import re
from typing import Final

DOMAIN: Final = "wud_queue"

SERVICE_ENQUEUE: Final = "enqueue"
SERVICE_STOP: Final = "stop"
SERVICE_RETRY: Final = "retry"
SERVICE_CLEAR: Final = "clear"

EVENT_FAILED: Final = "wud_queue_failed"
EVENT_FINISHED: Final = "wud_queue_finished"

# The queue's states, the sensor's state.
IDLE: Final = "idle"
RUNNING: Final = "running"
STOPPED: Final = "stopped"
FAILED: Final = "failed"
STATES: Final = [IDLE, RUNNING, STOPPED, FAILED]

# An item's statuses.
QUEUED: Final = "queued"
DONE: Final = "done"
NOT_RUN: Final = "not_run"

# Why an item failed.
REASON_SERVICE: Final = "service"
REASON_START: Final = "start"
REASON_VERSION: Final = "version"
REASON_TIMEOUT: Final = "timeout"

# WUD sets in_progress as soon as an install command arrives, so a minute without it means WUD is down or never got it.
START_TIMEOUT: Final = 60
# WUD may unmark a container a moment before it has looked at the new one, so the new version gets time to show.
VERSION_GRACE: Final = 120
# One install may take this long, a large image on a slow line included, before it counts as failed.
RUN_TIMEOUT: Final = 30 * 60
# A container whose entity is unavailable or gone when its turn comes waits this long for it, as Home Assistant drops
# an install for such an entity without a word. It covers WUD restarting after it has replaced itself.
AVAILABLE_TIMEOUT: Final = 5 * 60
# A finished or stopped queue stays on show this long, unless something new is queued first. A failed one stays.
KEEP_FINISHED: Final = 10 * 60
# How often a running queue looks at its install, besides whenever the update entity changes.
TICK: Final = 5

# WUD's own image, by its current name and the one it had before moving to getwud.
WUD_IMAGE: Final = re.compile(r"(^|/)(getwud/wud|fmartinou/whats-up-docker)$")
# The short-lived container WUD starts to replace itself is left behind with an update entity of its own, named like
# `wud-wud-self-update-1791730329826`. It shares WUD's image and project, but is never something to install.
SELF_UPDATE_HELPER: Final = re.compile(r"-self-update-\d+$")
