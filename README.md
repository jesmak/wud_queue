# WUD Queue

Home Assistant integration that installs What's Up Docker's container updates one at a time, notices when one fails,
and tells automations about it.

[![GitHub Release][releases-shield]][releases] [![GitHub Release Date][release-date-shield]][releases] [![HACS][hacs-shield]][hacs] [![License][license-shield]](LICENSE)

<img src="https://raw.githubusercontent.com/jesmak/wud_queue/main/docs/images/dashboard-light.png#gh-light-mode-only" width="560" alt="The WUD Queue card on a dashboard with a queue running, one container updated, a group of services updating and two containers queued">
<img src="https://raw.githubusercontent.com/jesmak/wud_queue/main/docs/images/dashboard-dark.png#gh-dark-mode-only" width="560" alt="The WUD Queue card on a dashboard with a queue running, one container updated, a group of services updating and two containers queued">

## What is it?

[What's Up Docker](https://github.com/getwud/wud) (WUD) finds updates for your containers and shows each one in Home
Assistant as an update entity with an install button. Pressing several of those buttons at once, or Home Assistant's
own "Update all", sends every install to WUD at the same time. WUD runs them side by side, its own update among them,
and compose files written in the same second can end up with the wrong versions in them.

WUD Queue puts the installs in a line. It installs one container at a time, the next only after the last has reached
its new version, and WUD's own container always last. WUD reports no failures, so the integration works them out from
what the update entities do, stops the queue at the first one, and fires an event about it. The queue lives in Home
Assistant, so it keeps going with every browser closed, and every dashboard shows the same queue.

The [WUD Queue card](https://github.com/jesmak/wud-queue-card) is its dashboard. It lists the updates by compose
project, queues them with a button, and shows the queue as it runs. The integration works without the card too,
through its actions.

## Installation

### With HACS

1. Add this repository to HACS custom repositories with type **Integration**
2. Search for WUD Queue in HACS and download it
3. Restart Home Assistant
4. In Settings, open Devices & services, add an integration and choose WUD Queue

### Manual

1. Copy `custom_components/wud_queue` from the source code of the [latest release][releases] to the
   `config/custom_components` folder of your Home Assistant installation
2. Restart Home Assistant
3. In Settings, open Devices & services, add an integration and choose WUD Queue

### What WUD needs

The queue works on the update entities WUD creates through MQTT discovery, so WUD needs its
[Home Assistant MQTT trigger](https://getwud.github.io/wud/configuration/triggers/homeassistant-mqtt/) with discovery
on, and Home Assistant needs the MQTT integration:

```yaml
environment:
    - WUD_TRIGGER_MQTT_HASS_URL=mqtt://homeassistant.local:1883
    - WUD_TRIGGER_MQTT_HASS_HASS_ENABLED=true
    - WUD_TRIGGER_MQTT_HASS_HASS_DISCOVERY=true
```

- Leave `WUD_TRIGGER_MQTT_<name>_HASS_ENTITIES` at its default, `all`. In `summary` mode WUD makes no update entity per
  container, and there is nothing to queue.
- WUD needs a trigger that can update the containers, such as its Docker or Docker Compose trigger. Without one, WUD
  takes the install, does nothing, and the queue reports it as failed.
- Keep `WUD_WATCHER_<name>_WATCHEVENTS` at its default, `true`. WUD then sees a container's new version as soon as it
  starts. Without the events it sees it only at its next scheduled watch, and an install that worked is reported as
  failed.

WUD reads a container's labels when it watches it. After changing a label such as `wud.trigger.exclude`, let WUD watch
the container again before queuing it, or WUD still acts on the old labels.

## Settings

There are none. The integration has one entry, which adds the queue's sensor and its actions.

## Sensors

`sensor.wud_queue`, on a device named WUD Queue, is the queue. Its state is one of:

| State     | Meaning                                                                         |
| --------- | ------------------------------------------------------------------------------- |
| `idle`    | Nothing is running. A finished queue stays on show for ten minutes              |
| `running` | Installing, one item at a time                                                  |
| `stopped` | Stopped by `wud_queue.stop`. The item being installed finished, the rest didn't |
| `failed`  | An item failed, and the queue stopped there                                     |

Its attributes:

| Attribute  | Description                                                      |
| ---------- | ---------------------------------------------------------------- |
| `items`    | The queue in order. Each item has the fields below               |
| `stopping` | `true` while a stop waits for the item being installed to finish |

| Item field | Description                                                                                                                            |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `name`     | The container's name, or the image's for a group of services                                                                           |
| `status`   | `queued`, `running`, `done`, `failed` or `not_run`                                                                                     |
| `reason`   | Why it failed, `service`, `start`, `version` or `timeout` (below)                                                                      |
| `error`    | Home Assistant's error, when it refused to send the install                                                                            |
| `entities` | Each container of the item, with its `entity_id`, the version it was on (`from`), the one it is headed for (`to`) and its own `status` |

An item is one container, or the services of one compose project that run the same image and are headed for the same
version, such as five workers built on one Node image. Its containers are installed one after another, and a stop
waits for the whole item, so a compose file is never left with only some of its services updated.

## Actions

| Action              | Description                                                                                      |
| ------------------- | ------------------------------------------------------------------------------------------------ |
| `wud_queue.enqueue` | Queues the updates of WUD update entities (`entity_id`, one or a list) and starts the queue      |
| `wud_queue.stop`    | Lets the item being installed finish and leaves the rest                                         |
| `wud_queue.retry`   | After a failure or a stop, queues the failed item and the ones that didn't run, and starts again |
| `wud_queue.clear`   | Empties a queue that isn't running, as happens by itself ten minutes after it finishes           |

`enqueue` adds to a running queue, and ignores entities already in it and ones without an update. A service of a
compose group that is installing joins that group's item. While a stop is waiting for its item to finish, `enqueue`
is refused, so nothing is queued only to be left out. When the queue
isn't running, it starts a new one in place of the finished, stopped or failed one. WUD's own container is always
moved to the end of what is still waiting.

## Events

| Event                | Fired                                        | Data                                                                                       |
| -------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `wud_queue_failed`   | When an item fails and the queue stops       | `name`, `entity_ids`, `reason`, `error`, `installed_version`, `target_version`             |
| `wud_queue_finished` | When the queue ends, done, stopped or failed | `done`, `failed` and `not_run`, the number of items of each, and `items`, as in the sensor |

## How a failure is recognised

WUD reports no failures. When an install command arrives, it marks the container as installing (`in_progress`), runs
its trigger, and unmarks it when the trigger ends, whether it worked or not. An install is done once the container is
on its new version. It fails, with its `reason`, when:

| Reason    | What happened                                                                                      |
| --------- | -------------------------------------------------------------------------------------------------- |
| `service` | Home Assistant refused to send the install, such as for an entity with no update                   |
| `start`   | WUD didn't mark the container as installing within a minute. WUD is down, or never got the command |
| `version` | WUD finished, but two minutes later the container was still on its old version                     |
| `timeout` | The install ran for half an hour without finishing                                                 |

After WUD has updated itself, its entity can stay marked as installing for good. The queue goes by the version, so
that install still counts as done. The mark has a cost, though. Home Assistant refuses to install an update that is marked
as installing, so WUD's next update fails with `service` and Home Assistant's message until WUD publishes its
container's state again, as it does when it restarts. While WUD recreates a container, its entity is briefly unavailable, and the queue
waits it out.

## Examples

### A notification when an update fails

```yaml
triggers:
    - trigger: event
      event_type: wud_queue_failed
actions:
    - action: notify.mobile_app_phone
      data:
          title: Container update failed
          message: >-
              {{ trigger.event.data.name }} is still on {{ trigger.event.data.installed_version }},
              not {{ trigger.event.data.target_version }} ({{ trigger.event.data.reason }}).
```

### Queue every waiting update at night

```yaml
triggers:
    - trigger: time
      at: "03:00:00"
conditions:
    - condition: state
      entity_id: sensor.wud_queue
      state: idle
actions:
    - action: wud_queue.enqueue
      data:
          entity_id: >-
              {{ states.update | selectattr('state', 'eq', 'on')
                 | selectattr('attributes.watcher', 'defined') | map(attribute='entity_id') | list }}
```

The action refuses a list with nothing to install, so the automation shows an error on a night without updates. Add
a template condition on the same list to skip those nights quietly.

## Data

Everything comes from the update entities WUD publishes over MQTT. The integration reads their installed and latest
versions, whether an install is running, and the compose project, service and image WUD adds as attributes. The integration fetches
nothing and talks to WUD only through `update.install`. The queue isn't kept over a restart of Home Assistant, as
what WUD did meanwhile can't be told, and a wrong guess would run an install twice.

## Translating

The integration's texts are in `custom_components/wud_queue/translations/`, one JSON file per language, named by its
language code, such as `en.json` and `fi.json`. To add a language, copy `en.json` to the new language's code and
translate the values.

## Development

Requires Python 3.14, the version the current Home Assistant needs.

```
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
```

| Path                                      | What it contains                                                 |
| ----------------------------------------- | ---------------------------------------------------------------- |
| `custom_components/wud_queue/queue.py`    | The queue: grouping, installing one at a time, and failures      |
| `custom_components/wud_queue/sensor.py`   | The queue's sensor                                               |
| `custom_components/wud_queue/__init__.py` | Setting up, and the actions                                      |
| `docs/screenshots.json`                   | The made-up data the README's image is taken from                |
| `tests/`                                  | Tests, run with pytest and WUD's update entities written by hand |

## Support

Hey dude! Help me out for a couple of :beers: or a :coffee:!

[![coffee](https://www.buymeacoffee.com/assets/img/custom_images/black_img.png)](https://www.buymeacoffee.com/jesmak)

## License

MIT, see [LICENSE](LICENSE).

[releases-shield]: https://img.shields.io/github/release/jesmak/wud_queue.svg?style=for-the-badge
[release-date-shield]: https://img.shields.io/github/release-date/jesmak/wud_queue?style=for-the-badge
[releases]: https://github.com/jesmak/wud_queue/releases
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://hacs.xyz/docs/faq/custom_repositories/
[license-shield]: https://img.shields.io/github/license/jesmak/wud_queue.svg?style=for-the-badge
