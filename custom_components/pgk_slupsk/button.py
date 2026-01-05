from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.helpers.entity import EntityCategory, generate_entity_id

from .const import DOMAIN, DEVICE_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Utwórz przyciski dla danej konfiguracji PGK Słupsk."""

    entry_id = entry.entry_id
    integration_name = entry.title
    coordinator = hass.data[DOMAIN][entry_id]

    button_api = PGKSlupskRefreshButton(
        hass=hass,
        coordinator=coordinator,
        integration_name=integration_name,
        entry_id=entry_id,
    )

    button_sensors = PGKSlupskSensorsRefreshButton(
        hass=hass,
        coordinator=coordinator,
        integration_name=integration_name,
        entry_id=entry_id,
    )
    
    button_cache = PGKSlupskClearCacheButton(
        hass, coordinator, integration_name, entry_id
    )

    async_add_entities([button_api, button_sensors, button_cache])


# --------------------------------------------------
#  PRZYCISK 1 — ODŚWIEŻENIE DANYCH Z API
# --------------------------------------------------

class PGKSlupskRefreshButton(ButtonEntity):
    """Przycisk do ręcznego odświeżenia danych z API PGK Słupsk."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:api"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        integration_name: str,
        entry_id: str,
    ) -> None:

        self._hass = hass
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._integration_name = integration_name

        self._attr_name = "Odśwież dane z API"
        self._attr_unique_id = f"{entry_id}_refresh_button"

        # entity_id
        self.entity_id = generate_entity_id(
            "button.{}",
            f"pgk_slupsk_{integration_name}_api_refresh",
            hass=self._hass,
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}::service")},
            name=DEVICE_NAME,
            manufacturer="PGK Słupsk",
            model=integration_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        """Obsługa kliknięcia."""
        _LOGGER.info("Przycisk: ręczne odświeżenie danych z API PGK Słupsk")
        await self._coordinator.retry_update_data()


# --------------------------------------------------
#  PRZYCISK 2 — ODŚWIEŻENIE SENSORÓW (bez API)
# --------------------------------------------------

class PGKSlupskSensorsRefreshButton(ButtonEntity):
    """Przycisk do ręcznego odświeżenia stanu sensorów (bez pobierania danych)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:api-off"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        integration_name: str,
        entry_id: str,
    ) -> None:

        self._hass = hass
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._integration_name = integration_name

        self._attr_name = "Odśwież sensory"
        self._attr_unique_id = f"{entry_id}_sensors_refresh_button"

        # entity_id
        self.entity_id = generate_entity_id(
            "button.{}",
            f"pgk_slupsk_{integration_name}_sensors_refresh",
            hass=self._hass,
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}::service")},
            name=DEVICE_NAME,
            manufacturer="PGK Słupsk",
            model=integration_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        """Ręczne odświeżenie stanu wszystkich sensorów."""
        _LOGGER.info("Przycisk: ręczne odświeżenie sensorów PGK Słupsk (bez API)")

        # wołamy Twoją istniejącą logikę!
        await self._coordinator._handle_sensors_midnight_refresh(datetime.now())

# --------------------------------------------------
# 3 — PRZYCISK: Wyczyść cache
# --------------------------------------------------

class PGKSlupskClearCacheButton(ButtonEntity):

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cached"

    def __init__(self, hass, coordinator, integration_name, entry_id):

        self._hass = hass
        self._coordinator = coordinator

        self._attr_name = "Wyczyść cache"
        self._attr_unique_id = f"{entry_id}_clear_cache"

        self.entity_id = generate_entity_id(
            "button.{}",
            f"pgk_slupsk_{integration_name}_clear_cache",
            hass=self._hass,
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}::service")},
            name=DEVICE_NAME,
            manufacturer="PGK Słupsk",
            model=integration_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        _LOGGER.info("Przycisk: usuwanie cache PGK Słupsk")
        await self._coordinator.clear_cache()