from __future__ import annotations

import logging
from typing import List, Dict

import voluptuous as vol

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, Context
from homeassistant.helpers import entity_registry as er, config_validation as cv
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_NOTIFY_SERVICE = "notify_service"

# Techniczna nazwa typu akcji (bez polskich znaków, bez spacji – mniej problemów)
ACTION_SEND_NOTIFICATION = "send_waste_pickup_notification"
ACTION_TYPES = {ACTION_SEND_NOTIFICATION}

# Base schema dla akcji urządzenia:
# DEVICE_ACTION_BASE_SCHEMA dodaje device_id + domain
ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        # Uwaga: tutaj NIE ma entity_id
        vol.Required(CONF_NOTIFY_SERVICE): cv.string,
    }
)


async def async_get_actions(hass: HomeAssistant, device_id: str) -> List[Dict]:
    """Lista akcji dostępnych dla urządzenia PGK."""

    registry: er.EntityRegistry = er.async_get(hass)
    entries = er.async_entries_for_device(registry, device_id)

    actions: List[Dict] = []

    _LOGGER.debug(
        "PGK device_action: device_id=%s, liczba entries=%d",
        device_id,
        len(entries),
    )

    # Sprawdzamy, czy urządzenie ma sensor *_waste_tomorrow
    has_waste_tomorrow = any(
        entry.unique_id and entry.unique_id.endswith("_waste_tomorrow")
        for entry in entries
    )

    if not has_waste_tomorrow:
        _LOGGER.debug(
            "PGK device_action: brak sensora *_waste_tomorrow dla device_id=%s",
            device_id,
        )
        return actions

    # Jeśli jest taki sensor → dodajemy jedną akcję urządzenia
    actions.append(
        {
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_SEND_NOTIFICATION,
            # CONF_NOTIFY_SERVICE user uzupełni w UI
        }
    )

    _LOGGER.debug(
        "PGK device_action: dla device_id=%s zwracam %d akcji",
        device_id,
        len(actions),
    )

    return actions


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> Dict[str, vol.Schema]:
    """Dodatkowe pola konfiguracyjne akcji (widoczne w UI)."""

    if config.get(CONF_TYPE) == ACTION_SEND_NOTIFICATION:
        return {
            "extra_fields": vol.Schema(
                {
                    vol.Required(CONF_NOTIFY_SERVICE): cv.string,
                }
            )
        }

    return {"extra_fields": vol.Schema({})}


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    """Wykonaj akcję urządzenia."""

    # Uwaga: NIE robimy tutaj config = ACTION_SCHEMA(config)
    # Core już użył ACTION_SCHEMA.

    action_type: str = config[CONF_TYPE]
    device_id: str = config[CONF_DEVICE_ID]
    notify_service: str = config[CONF_NOTIFY_SERVICE]

    _LOGGER.debug(
        "PGK device_action: call_action type=%s device_id=%s notify_service=%s",
        action_type,
        device_id,
        notify_service,
    )

    if action_type != ACTION_SEND_NOTIFICATION:
        _LOGGER.warning(
            "PGK device_action: nieobsługiwany typ akcji: %s", action_type
        )
        return

    registry: er.EntityRegistry = er.async_get(hass)
    entries = er.async_entries_for_device(registry, device_id)

    entity_id: str | None = None

    # Szukamy sensora *_waste_tomorrow dla tego urządzenia
    for entry in entries:
        if entry.unique_id and entry.unique_id.endswith("_waste_tomorrow"):
            entity_id = entry.entity_id
            break

    if not entity_id:
        _LOGGER.warning(
            "PGK device_action: nie znaleziono encji *_waste_tomorrow dla device_id=%s",
            device_id,
        )
        return

    state = hass.states.get(entity_id)
    value = (state.state or "").strip().lower() if state else ""
    
    # Jeśli wartość to "brak" → NIE wysyłamy powiadomienia
    if value == "brak":
        return
    
    # Każda inna wartość → wysyłamy
    if "." in notify_service:
        domain, service_name = notify_service.split(".", 1)
    else:
        domain, service_name = "notify", notify_service
    
    await hass.services.async_call(
        domain,
        service_name,
        {
            "message": f"Wystaw odpady. Jutro odbiór: {value}",
        },
        context=context,
    )
    
