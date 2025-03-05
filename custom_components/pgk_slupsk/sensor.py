"""Platforma sensorów dla integracji PGK Słupsk."""
from datetime import datetime, timedelta
import logging
import asyncio

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import generate_entity_id

import aiohttp
from .const import DOMAIN, WASTE_TYPES, DAYS_TRANSLATION

_LOGGER = logging.getLogger(__name__)

RETRY_INTERVAL = 600  # 10 minut w sekundach

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Ustawienie sensorów na podstawie wpisu w konfiguracji."""
    street_id = entry.data.get("street_id")
    integration_name = entry.title
    _LOGGER.debug("Konfiguracja street_id: %s", street_id)
    _LOGGER.debug("Konfiguracja integration_name: %s", integration_name)

    coordinator = PGKSlupskCoordinator(hass, street_id)
    await coordinator.async_config_entry_first_refresh()

    sensors = [
        PGKSlupskSensor(coordinator, waste_type_id, waste_data, integration_name, hass)
        for waste_type_id, waste_data in coordinator.data.items()
    ]
    
    # Dodanie sensora dla odpady do przygotowania
    day_before_sensor = PGKSlupskDayBeforeSensor(coordinator, integration_name, hass)
    
    # Zarejestrowanie sensorów w koordynatorze
    coordinator.sensors = sensors + [day_before_sensor]
    
    async_add_entities(sensors + [day_before_sensor], update_before_add=True)

class PGKSlupskCoordinator(DataUpdateCoordinator):
    """Koordynator do zarządzania danymi z API."""

    def __init__(self, hass, street_id):
        self.street_id = street_id
        #self._update_api_data_time = None  # Dodanie atrybutu do przechowywania czasu pobrania danych
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(days=1),
        )
        self.sensors = []  # Będziemy przechowywać referencje do sensorów
        hass.loop.create_task(self.schedule_midnight_refresh())

    async def schedule_midnight_refresh(self):
        """Zapewnij odświeżenie danych dokładnie 10 sekund po północy."""
        while True:
            now = datetime.now()
            next_refresh = (now + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
            seconds_until_refresh = (next_refresh - now).total_seconds()

            await asyncio.sleep(seconds_until_refresh)

            # Wymuś odświeżenie danych z API
            _LOGGER.debug("Rozpoczynanie odświeżania danych 10 sekund po północy")
            _LOGGER.debug("Harmonogram działa. Następne odświeżenie: %s", next_refresh)
            await self.retry_update_data()

    async def retry_update_data(self):
        """Ponawiaj próbę odświeżenia danych co 10 minut w przypadku niepowodzenia."""
        while True:
            try:
                await self.async_request_refresh()
                _LOGGER.info("PGK Słupsk - dane zostały pomyślnie odświeżone z API")
                break  # Wyjście z pętli po udanym odświeżeniu
            except UpdateFailed as e:
                _LOGGER.error("Błąd odświeżania danych: %s. Próba ponowienia za 10 minut.", e)
                await asyncio.sleep(RETRY_INTERVAL)

    async def _async_update_data(self):
        """Pobierz dane z API i przetwórz je."""
        start_date = datetime.now().strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d")
        url = f"https://pgkwywozy.infocity.pl/Api/GetNewest/{self.street_id}?start={start_date}&end={end_date}"
        headers = {
            "User-Agent": "WebKit=Android",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=20) as response:
                    response.raise_for_status()
                    data = await response.json()

                    # Dodano log dla danych surowych
                    _LOGGER.debug(f"Surowe dane z API: {data}")

                    processed_data = {}
                    for entry in data:
                        waste_type_id = entry["TypOdpaduId"]
                        if waste_type_id not in processed_data:
                            processed_data[waste_type_id] = {
                                "TypOdpadu": entry["TypOdpadu"],
                                "Kolor": entry["Kolor"],
                                #"Kolor": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "Daty": [],
                                "Updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            }
                        processed_data[waste_type_id]["Daty"].append(entry["Data"])

                    # Dodano log dla przetworzonych danych
                    _LOGGER.debug(f"Przetworzone dane z API: {processed_data}")
                    
                    _LOGGER.info(f"PGK Słupsk - dane zostały odświeżone z API start={start_date}&end={end_date}")
                    
                    # Zapisujemy czas aktualizacji danych
                    #self._update_api_data_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    # Aktualizacja danych w koordynatorze
                    self.async_set_updated_data(processed_data)

                    # Po pobraniu danych, wymusimy odświeżenie wszystkich sensorów
                    for sensor in self.sensors:
                        await sensor.async_update()

                    return processed_data

        except aiohttp.ClientError as err:
            _LOGGER.error(f"Błąd podczas pobierania danych z API: {err}")
            raise UpdateFailed(f"Błąd podczas aktualizacji danych: {err}") from err

class PGKSlupskSensor(CoordinatorEntity, SensorEntity):
    """Sensor dla każdego typu odpadu."""

    def __init__(self, coordinator, waste_type_id, waste_data, integration_name, hass):
        super().__init__(coordinator)
        self._waste_type_id = waste_type_id
        self._waste_data = waste_data
        self._integration_name = integration_name
        self._hass = hass
        waste_info = WASTE_TYPES.get(waste_type_id, {})
        self._name = f"{waste_info.get('name', waste_data['TypOdpadu'])}"
        self._icon = waste_info.get("icon", "mdi:trash-can")
        self._color = waste_data["Kolor"]
        self._updated = waste_data["Updated"]
        self._dates = sorted(waste_data["Daty"])
        self.entity_id = generate_entity_id(
            "sensor.{}",
            f"pgk_slupsk_{self._integration_name}_{self._name}",
            hass=self._hass,
        )
        # Dodano atrybut update_time, który będzie aktualizowany przy każdym odświeżeniu
        # self._update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self.entity_id

    @property
    def state(self):
        """Zwróć stan sensora jako opis najbliższego odbioru."""
        if self._dates:
            next_date = datetime.strptime(self._dates[0], "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            days_diff = round((next_date - today).days)
            weekday_name = DAYS_TRANSLATION[next_date.weekday()]
            if days_diff == 0:
                _LOGGER.debug(f"Sensor {self.name} - stan: dziś")
                return "dziś"
            elif days_diff == 1:
                _LOGGER.debug(f"Sensor {self.name} - stan: jutro")
                return "jutro"
            elif days_diff == 2:
                _LOGGER.debug(f"Sensor {self.name} - stan: {weekday_name}, pojutrze")
                return f"{weekday_name}, pojutrze"
            elif days_diff > 2:
                _LOGGER.debug(f"Sensor {self.name} - stan: {weekday_name}, za {days_diff} dni")
                return f"{weekday_name}, za {days_diff} dni"
        _LOGGER.debug(f"Sensor {self.name} - stan: brak danych")
        return "brak danych"

    @property
    def extra_state_attributes(self):
        """Zwróć dodatkowe atrybuty."""
        _LOGGER.debug(f"Zaktualizowane atrybuty sensora {self.name}: Kolor: {self._color}, Daty odbioru: {self._dates}")
        return {
            "Kolor": self._color,
            "Daty odbioru": self._dates,
            "Data": self._dates[0] if self._dates else None,
            "TypOdpaduId": self._waste_type_id,
            "Updated from API": self._updated,  # Dodany atrybut updated
            #"Update API Data Time": self.coordinator._update_api_data_time,  # Dodany atrybut update_api_data_time
        }

    @property
    def icon(self):
        return self._icon

    async def async_update(self):
        """Zaktualizuj dane sensora przy każdym odświeżeniu."""
        await super().async_update()  # Odśwież dane z koordynatora
        waste_data = self.coordinator.data.get(self._waste_type_id, {})
        
        # Sprawdzamy, czy dane są zaktualizowane
        if waste_data:
            self._color = waste_data.get("Kolor", self._color)  # Aktualizacja koloru, jeśli się zmienił
            self._dates = sorted(waste_data.get("Daty", self._dates))  # Aktualizacja dat, jeśli się zmieniły
            self._updated = waste_data.get("Updated", self._updated)

        self._update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Zaktualizuj czas aktualizacji sensora
        _LOGGER.debug(f"Sensor {self._name} - czas aktualizacji: {self._update_time}")

class PGKSlupskDayBeforeSensor(CoordinatorEntity, SensorEntity):
    """Sensor dla odpadów, które mają być zabrane na następny dzień."""

    def __init__(self, coordinator, integration_name, hass):
        super().__init__(coordinator)
        self._integration_name = integration_name
        self._hass = hass
        self.entity_id = generate_entity_id(
            "sensor.{}",
            f"pgk_slupsk_{self._integration_name}_wtp",
            hass=self._hass,
        )

    @property
    def name(self):
        return "Odpady do przygotowania"

    @property
    def unique_id(self):
        return self.entity_id

    @property
    def state(self):
        """Zwróć stan sensora jako listę typów odpadów do zabrania na następny dzień."""
        next_day = datetime.now() + timedelta(days=1)
        waste_types_for_next_day = []

        for waste_type_id, waste_data in self.coordinator.data.items():
            for waste_date in waste_data["Daty"]:
                waste_date_obj = datetime.strptime(waste_date, "%Y-%m-%d")
                if waste_date_obj.date() == next_day.date():
                    waste_info = WASTE_TYPES.get(waste_type_id, {})
                    waste_types_for_next_day.append(waste_info.get("name", waste_data["TypOdpadu"]))

        _LOGGER.debug(f"Sensor Odpady do przygotowania - dane na jutro: {waste_types_for_next_day}")
        return ", ".join(waste_types_for_next_day) if waste_types_for_next_day else "none"

    @property
    def icon(self):
        return "mdi:calendar-check"
