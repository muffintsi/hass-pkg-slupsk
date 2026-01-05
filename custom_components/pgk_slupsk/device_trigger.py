from __future__ import annotations

import logging
from typing import List, Dict

import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
)
from homeassistant.components.homeassistant.triggers import (
    state as state_trigger,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Typ triggera
TRIGGER_WASTE_TOMORROW = "waste_pickup_tomorrow"

TRIGGER_TYPES = [TRIGGER_WASTE_TOMORROW]

# Schema dla triggera urządzenia
TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
    }
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> List[Dict]:
    """Zwróć listę triggerów dostępnych dla danego urządzenia."""

    registry: er.EntityRegistry = er.async_get(hass)
    entries = er.async_entries_for_device(registry, device_id)

    triggers: List[Dict] = []

    _LOGGER.debug(
        "PGK device_trigger: device_id=%s, znaleziono %d encji",
        device_id,
        len(entries),
    )

    for entry in entries:
        _LOGGER.debug(
            "PGK device_trigger: sprawdzam entity_id=%s, domain=%s, unique_id=%s",
            entry.entity_id,
            entry.domain,
            entry.unique_id,
        )

        # 🔥 TU DZIAŁA FILTR:
        # w sensors.py: self._unique_id = f"{entry_id}_waste_tomorrow"
        # więc sprawdzamy, czy unique_id kończy się na '_waste_tomorrow'
        if entry.unique_id and entry.unique_id.endswith("_waste_tomorrow"):
            _LOGGER.debug(
                "PGK device_trigger: dopasowano sensor waste_tomorrow: %s",
                entry.entity_id,
            )

            triggers.append(
                {
                    # pola wymagane przez DEVICE_TRIGGER_BASE_SCHEMA
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_ENTITY_ID: entry.entity_id,
                    # pola wymagane przez TRIGGER_SCHEMA
                    CONF_TYPE: TRIGGER_WASTE_TOMORROW,
                }
            )

    _LOGGER.debug(
        "PGK device_trigger: dla device_id=%s zwracam %d trigger(ów)",
        device_id,
        len(triggers),
    )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Podpinanie logiki triggera."""

    config = TRIGGER_SCHEMA(config)
    trigger_type: str = config[CONF_TYPE]
    entity_id: str = config[CONF_ENTITY_ID]

    _LOGGER.debug(
        "PGK device_trigger: attach_trigger type=%s, entity_id=%s",
        trigger_type,
        entity_id,
    )

    if trigger_type == TRIGGER_WASTE_TOMORROW:
        # zamiast ręcznie subskrybować, używamy wbudowanego state_trigger
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                state_trigger.CONF_TO: "tomorrow",
            }
        )

        return await state_trigger.async_attach_trigger(
            hass,
            state_config,
            action,
            trigger_info,
            platform_type="device",
        )

    # teoretycznie nieosiągalne, bo schema pilnuje typu
    _LOGGER.warning(
        "PGK device_trigger: nieobsługiwany trigger type=%s", trigger_type
    )

    def _noop() -> None:
        return None

    return _noop
