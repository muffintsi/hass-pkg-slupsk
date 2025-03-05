import logging
from homeassistant import config_entries
from homeassistant.core import callback
import aiohttp
import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class PGKSlupskConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Przepływ konfiguracji dla PGK Słupsk."""

    async def async_step_user(self, user_input=None):
        """Pierwszy krok konfiguracji - wybór miasta."""
        errors = {}

        if user_input is not None:
            # Zapisujemy city_id i przechodzimy do następnego kroku (wybór ulicy)
            city_id = user_input["city_id"]
            self.context["city_id"] = city_id

            # Pobieramy dostępne ulice
            streets = await self.get_streets(city_id)

            # Wyświetlamy listę ulic w formularzu
            return self.async_show_form(
                step_id="city",
                data_schema=vol.Schema({
                    vol.Required("street_id"): vol.In({street["id"]: street["name"] for street in streets}),
                }),
                errors=errors,
            )

        # Pobieramy listę miast
        cities = await self.get_cities()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("city_id"): vol.In({city["id"]: city["name"] for city in cities}),
            }),
            errors=errors,
        )

    async def async_step_city(self, user_input=None):
        """Drugi krok konfiguracji - wybór ulicy."""
        errors = {}

        if user_input is not None:
            street_id = user_input["street_id"]
            city_id = self.context["city_id"]

            # Pobieramy nazwę miasta oraz ulicy
            city_name = next(city["name"] for city in await self.get_cities() if city["id"] == city_id)
            street_name = next(street["name"] for street in await self.get_streets(city_id) if street["id"] == street_id)

            # Zapisujemy city_id i street_id w konfiguracji
            return self.async_create_entry(
                title=f"{city_name} - {street_name}",
                data={"city_id": city_id, "street_id": street_id},
            )

        # Pobieramy dostępne ulice na podstawie city_id
        city_id = self.context.get("city_id")
        streets = await self.get_streets(city_id)

        return self.async_show_form(
            step_id="city",
            data_schema=vol.Schema({
                vol.Required("street_id"): vol.In({street["id"]: street["name"] for street in streets}),
            }),
            errors=errors,
        )

    async def get_cities(self):
        """Pobierz dostępne miasta z API."""
        url = "https://pgkwywozy.infocity.pl/Api/GetCities"
        headers = {
            "User-Agent": "WebKit=Android",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                    _LOGGER.debug(f"Odpowiedź z API GetCities: {data}")

                    # Tworzymy listę miast
                    cities = [{"id": city["Id"], "name": city["Nazwa"]} for city in data]
                    return cities
        except aiohttp.ClientError as err:
            _LOGGER.error(f"Błąd podczas pobierania miast: {err}")
            return []

    async def get_streets(self, city_id):
        """Pobierz dostępne ulice dla wybranego miasta z API."""
        url = f"https://pgkwywozy.infocity.pl/Api/GetStreets/{city_id}?orderInfo=true"
        headers = {
            "User-Agent": "WebKit=Android",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                    _LOGGER.debug(f"Odpowiedź z API GetStreets: {data}")

                    # Tworzymy listę ulic z odpowiedzi
                    streets = [{"id": street["Id"], "name": street["Nazwa"]} for street in data]
                    return streets
        except aiohttp.ClientError as err:
            _LOGGER.error(f"Błąd podczas pobierania ulic: {err}")
            return []

    @staticmethod
    @callback
    def add_schema():
        """Zwraca schemat formularza."""
        import voluptuous as vol
        return vol.Schema({})
