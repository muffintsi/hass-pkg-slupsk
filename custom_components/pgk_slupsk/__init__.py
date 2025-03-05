"""Inicjalizacja integracji PGK Słupsk."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from .sensor import PGKSlupskCoordinator
import logging

_LOGGER = logging.getLogger(__name__)

DOMAIN = "pgk_slupsk"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Ustawienie integracji przy użyciu wpisu w konfiguracji."""
    hass.data.setdefault(DOMAIN, {})

    # Pobierz 'street_id' z konfiguracji
    street_id = entry.data.get("street_id")

    # Tworzenie instancji koordynatora z przekazanym 'street_id'
    coordinator = PGKSlupskCoordinator(hass, street_id)
    
    try:
        # Próba pierwszego odświeżenia danych
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.error("Nie udało się zainicjalizować danych: %s", err)
        raise ConfigEntryNotReady from err

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Przekazanie platform do konfiguracji
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Rozładowanie integracji."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
