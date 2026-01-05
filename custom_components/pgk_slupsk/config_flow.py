import logging
from typing import Any, Dict, List, Optional

import aiohttp
import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


TOKEN_URL = "https://pgkslupsk.pl/api/createToken"
CMS_GRAPHQL_URL = "https://cms.pgkslupsk.pl/graphql"

HEADERS_BASE = {
    "User-Agent": "WebKit=Android",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

QUERY_REGIONS = """
query Regions {
  PGKExtended {
    wasteSchedulesRegionsPGK {
      regions {
        id
        name
      }
    }
  }
}
"""

QUERY_LOCATIONS = """
query getLocations($regionName: String!, $searchTerm: String!) {
  PGKExtended {
    wasteSchedulesLocationsByRegionPGK(
      regionName: $regionName
      searchTerm: $searchTerm
    ) {
      locations
    }
  }
}
"""


async def _post_json(
    session: aiohttp.ClientSession,
    url: str,
    headers: Dict[str, str],
    payload: Any,
) -> Dict[str, Any]:
    async with session.post(
        url,
        headers=headers,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        # API czasem zwraca z błędnym content-type, więc wymuszamy None
        return await resp.json(content_type=None)


async def _create_token(session: aiohttp.ClientSession) -> Optional[str]:
    try:
        data = await _post_json(session, TOKEN_URL, HEADERS_BASE, {})
        return (data or {}).get("token")
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("PGK token error: %s", err)
        return None


async def _fetch_regions(session: aiohttp.ClientSession, token: Optional[str]) -> List[str]:
    headers = dict(HEADERS_BASE)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {"operationName": "Regions", "query": QUERY_REGIONS, "variables": {}}
    data = await _post_json(session, CMS_GRAPHQL_URL, headers, payload)

    regions = (
        (data or {}).get("data", {})
        .get("PGKExtended", {})
        .get("wasteSchedulesRegionsPGK", {})
        .get("regions", [])
    )

    names: List[str] = []
    if isinstance(regions, list):
        for r in regions:
            if isinstance(r, dict) and r.get("name"):
                names.append(str(r["name"]))

    # Usuwamy duplikaty (case-insensitive) i sortujemy stabilnie
    uniq: Dict[str, str] = {}
    for n in names:
        key = n.strip().casefold()
        if key and key not in uniq:
            uniq[key] = n.strip()

    return sorted(uniq.values())


async def _fetch_locations(
    session: aiohttp.ClientSession,
    token: Optional[str],
    region: str,
    search_term: str,
) -> List[str]:
    headers = dict(HEADERS_BASE)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "operationName": "getLocations",
        "query": QUERY_LOCATIONS,
        "variables": {"regionName": region, "searchTerm": search_term},
    }

    data = await _post_json(session, CMS_GRAPHQL_URL, headers, payload)
    locs = (
        (data or {}).get("data", {})
        .get("PGKExtended", {})
        .get("wasteSchedulesLocationsByRegionPGK", {})
        .get("locations", [])
    )

    if not isinstance(locs, list):
        return []

    # Prosty uniq + sort
    out = sorted({str(x).strip() for x in locs if x})
    return out


class PGKSlupskConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow dla PGK Słupsk (nowe API: region + location)."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Krok 1: wybór regionu."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self.context["region"] = user_input["region"]
            return await self.async_step_location_search()

        async with aiohttp.ClientSession() as session:
            token = await _create_token(session)
            regions = await _fetch_regions(session, token)

        if not regions:
            errors["base"] = "cannot_connect"
            regions = ["(brak danych)"]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("region"): vol.In(regions)}),
            errors=errors,
        )

    async def async_step_location_search(self, user_input=None):
        """Krok 2: wpisanie frazy do wyszukiwania ulicy/lokalizacji."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            search_term = (user_input.get("search_term") or "").strip()
            if not search_term:
                errors["search_term"] = "required"
            else:
                self.context["search_term"] = search_term
                return await self.async_step_location_select()

        return self.async_show_form(
            step_id="location_search",
            data_schema=vol.Schema({vol.Required("search_term"): str}),
            errors=errors,
        )

    async def async_step_location_select(self, user_input=None):
        """Krok 3: wybór location z listy wyników wyszukiwania."""
        errors: Dict[str, str] = {}

        region = self.context.get("region")
        search_term = self.context.get("search_term")
        if not region or not search_term:
            return await self.async_step_user()

        if user_input is not None:
            location = user_input["location"]
            title = f"{region} - {location}"
            return self.async_create_entry(
                title=title,
                data={
                    "type": "individual",
                    "region": region,
                    "location": location,
                },
            )

        async with aiohttp.ClientSession() as session:
            token = await _create_token(session)
            locations = await _fetch_locations(session, token, region, search_term)

        if not locations:
            errors["base"] = "no_results"
            # Pozwól wrócić do wyszukiwania bez crasha
            return self.async_show_form(
                step_id="location_select",
                data_schema=vol.Schema({vol.Required("location"): vol.In({})}),
                errors=errors,
                description_placeholders={
                    "region": str(region),
                    "search_term": str(search_term),
                },
            )

        return self.async_show_form(
            step_id="location_select",
            data_schema=vol.Schema({vol.Required("location"): vol.In(locations)}),
            errors=errors,
        )
