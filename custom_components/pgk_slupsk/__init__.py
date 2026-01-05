"""Inicjalizacja integracji PGK Słupsk."""
from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .sensor import PGKSlupskCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Ustawienie integracji przy użyciu wpisu w konfiguracji."""
    hass.data.setdefault(DOMAIN, {})

    # Nowe API: region + location
    region = entry.data.get("region")
    location = entry.data.get("location")
    customer_type = entry.data.get("type", "individual")
    if not region or not location:
        raise ConfigEntryNotReady(
            "Brak wartości region/location w konfiguracji integracji PGK Słupsk"
        )

    integration_name = entry.title or "PGK Słupsk"
    entry_id = entry.entry_id

    _LOGGER.debug(
        "Inicjalizacja PGK Słupsk: entry_id=%s, region=%s, location=%s, integration_name=%s",
        entry_id,
        region,
        location,
        integration_name,
    )

    # Tworzenie instancji koordynatora (nowe API: region + location)
    coordinator = PGKSlupskCoordinator(
        hass=hass,
        customer_type=str(customer_type),
        region=str(region),
        location=str(location),
        entry_id=entry_id,
        integration_name=integration_name,
    )

    try:
        # Próba pierwszego odświeżenia danych (pobranie z API, zapis JSON + ETag do plików)
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Nie udało się zainicjalizować danych PGK Słupsk: %s", err)
        raise ConfigEntryNotReady from err

    # Zachowujemy koordynator w hass.data, żeby czujniki i kalendarz mogły go wykorzystać
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Przekazanie platform do konfiguracji: sensory + kalendarz
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "calendar", "button"])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Rozładowanie integracji."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, ["sensor", "calendar", "button"]
        # jeśli w przyszłości dojdą inne platformy, dopisujemy je tutaj
    )

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Sprzątanie po usunięciu wpisu konfiguracji.

    Usuwa powiązane pliki JSON i ETag dla danej instancji integracji.
    """
    entry_id = entry.entry_id

    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base_dir, f"pgk_slupsk_{entry_id}.json")
    etag_path = os.path.join(base_dir, f"pgk_slupsk_{entry_id}.etag")

    for path in (json_path, etag_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                _LOGGER.debug(
                    "PGK Słupsk: usunięto plik %s dla wpisu %s",
                    path,
                    entry_id,
                )
        except OSError as err:
            _LOGGER.warning(
                "PGK Słupsk: nie udało się usunąć pliku %s dla wpisu %s: %s",
                path,
                entry_id,
                err,
            )
