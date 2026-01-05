"""Platforma sensorów dla integracji PGK Słupsk.

Zmiany:
* dokładny JSON z API jest zapisywany do pliku pgk_slupsk_<entry_id>.json,
* ETag zapisywany do pliku pgk_slupsk_<entry_id>.etag,
* przy aktualizacji używany jest nagłówek If-None-Match:
  - 304 => czytamy JSON z pliku, pracujemy tylko na nim,
  - 200 => zapisujemy nowy JSON + ETag, odświeżamy sensory,
* w przypadku błędu API używamy danych z lokalnego pliku (jeśli istnieje),
* unique_id sensorów jest stabilne (entry_id + typ odpadu) => brak duplikatów przy „Wczytaj ponownie”.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, time, date
from typing import Any, Dict, List

import aiohttp
import re
from urllib.parse import quote
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.event import async_track_time_change, async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, WASTE_TYPES, DAYS_TRANSLATION, DEVICE_NAME

_LOGGER = logging.getLogger(__name__)

RETRY_INTERVAL = 600  # 10 minut w sekundach

# Nowe API (RSC/Flight)
BASE_SITE = "https://pgkslupsk.pl"
PATH = "/harmonogram-odbioru-odpadow"


def _build_rsc_url(customer_type: str, region: str, location: str) -> str:
    return (
        f"{BASE_SITE}{PATH}"
        f"?type={quote(customer_type)}"
        f"&region={quote(region)}"
        f"&location={quote(location)}"
    )


_SCHEDULE_DATA_RE = re.compile(r'"scheduleData"\s*:\s*{')


def _extract_balanced_object(text: str, start_index: int) -> str:
    """Wyciąga substring JSON obiektu {...} od start_index (na '{') do pasującej '}'."""
    if start_index < 0 or start_index >= len(text) or text[start_index] != "{":
        raise ValueError("start_index nie wskazuje na '{'")

    depth = 0
    in_str = False
    esc = False

    for i in range(start_index, len(text)):
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_index : i + 1]

    raise ValueError("Nie udało się domknąć obiektu JSON (brak pasującej '}').")


def _extract_schedule_data_from_rsc(rsc_text: str) -> Dict[str, Any]:
    m = _SCHEDULE_DATA_RE.search(rsc_text)
    if not m:
        raise ValueError('Nie znalazłem pola "scheduleData" w RSC.')
    obj_start = rsc_text.find("{", m.end() - 1)
    obj_str = _extract_balanced_object(rsc_text, obj_start)
    return json.loads(obj_str)


def _build_fraction_index(node: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """code -> {name,color}; scheduleFractions is a GraphQL Connection (edges[].node)."""
    out: Dict[str, Dict[str, str]] = {}
    sf = node.get("scheduleFractions")
    if not isinstance(sf, dict):
        return out
    edges = sf.get("edges")
    if not isinstance(edges, list):
        return out
    for e in edges:
        if not isinstance(e, dict):
            continue
        n = e.get("node")
        if not isinstance(n, dict):
            continue
        code = (n.get("code") or "").strip()
        if not code:
            continue
        out[code] = {
            "name": (n.get("name") or "").strip(),
            "color": (n.get("color") or "").strip(),
        }
    return out


def _convert_schedule_to_legacy(schedule_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Konwersja node.schedule -> stary format JSON."""
    node = schedule_data.get("node")
    if not isinstance(node, dict):
        raise ValueError("scheduleData.node nie jest dict")

    frac_idx = _build_fraction_index(node)
    schedule = node.get("schedule")
    if not isinstance(schedule, list):
        raise ValueError("node.schedule nie jest listą")

    legacy: List[Dict[str, Any]] = []
    hid = 1

    def add_entry(date_str: str, code: str) -> None:
        nonlocal hid
        meta = frac_idx.get(code, {"name": "", "color": ""})
        legacy.append(
            {
                "HarmonogramId": hid,
                "AkcjaId": 1,
                "Akcja": "HARMONOGRAM",
                "TypOdpaduId": code,
                "TypOdpadu": meta.get("name", ""),
                "Kolor": meta.get("color", ""),
                "Data": date_str,
            }
        )
        hid += 1

    for month in schedule:
        if not isinstance(month, dict):
            continue
        year = month.get("year")
        month_no = month.get("monthNumber")
        if not isinstance(year, int) or not isinstance(month_no, int):
            continue

        day_items = month.get("dayItems")
        if not isinstance(day_items, list):
            continue

        for day in day_items:
            if not isinstance(day, dict):
                continue
            day_no = day.get("dayNumber")
            if not isinstance(day_no, int):
                continue

            date_str = f"{year}-{month_no:02d}-{day_no:02d}"

            fractions_map = day.get("fractionsMap")
            if not isinstance(fractions_map, list):
                continue

            for frac in fractions_map:
                if not isinstance(frac, dict):
                    continue
                parent_code = (frac.get("code") or "").strip()
                if not parent_code:
                    continue

                # rodzic zawsze jako wpis
                add_entry(date_str, parent_code)

                # childFractions: jeśli code != null => dodatkowy wpis (ta sama data)
                # unikamy dubla, gdy child == parent
                child_list = frac.get("childFractions") or []
                if isinstance(child_list, list):
                    for child in child_list:
                        if not isinstance(child, dict):
                            continue
                        child_code = child.get("code")
                        if not isinstance(child_code, str) or not child_code.strip():
                            continue
                        child_code = child_code.strip()
                        if child_code == parent_code:
                            continue
                        add_entry(date_str, child_code)

    legacy.sort(key=lambda r: (r.get("Data", ""), r.get("TypOdpaduId", "")))
    for i, row in enumerate(legacy, start=1):
        row["HarmonogramId"] = i

    return legacy


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Ustawienie sensorów na podstawie wpisu w konfiguracji."""
    integration_name = entry.title
    entry_id = entry.entry_id

    region = entry.data.get("region")
    location = entry.data.get("location")
    customer_type = entry.data.get("type", "individual")

    _LOGGER.debug("Konfiguracja region: %s", region)
    _LOGGER.debug("Konfiguracja location: %s", location)
    _LOGGER.debug("Konfiguracja type: %s", customer_type)
    _LOGGER.debug("Konfiguracja integration_name: %s", integration_name)
    _LOGGER.debug("Konfiguracja entry_id: %s", entry_id)

    # Używamy koordynatora stworzonego w __init__.py
    coordinator: PGKSlupskCoordinator = hass.data[DOMAIN][entry_id]

    # W tym momencie coordinator.data powinno być już zasilone przez
    # async_config_entry_first_refresh w __init__.py
    sensors: List[SensorEntity] = [
        PGKSlupskSensor(
            coordinator=coordinator,
            waste_type_id=waste_type_id,
            waste_data=waste_data,
            integration_name=integration_name,
            hass=hass,
            entry_id=entry_id,
        )
        for waste_type_id, waste_data in (coordinator.data or {}).items()
    ]

    # Dodanie sensora dla odpadów do przygotowania na następny dzień
    day_before_sensor = PGKSlupskDayBeforeSensor(
        coordinator=coordinator,
        integration_name=integration_name,
        hass=hass,
        entry_id=entry_id,
    )

    # Zarejestrowanie sensorów w koordynatorze (do ręcznego odświeżania po zmianie danych)
    coordinator.sensors = sensors + [day_before_sensor]

    async_add_entities(sensors + [day_before_sensor], update_before_add=True)


class PGKSlupskCoordinator(DataUpdateCoordinator[Dict[str, Dict[str, Any]]]):
    """Koordynator do zarządzania danymi z API i lokalnymi plikami JSON/ETag."""

    def __init__(
        self,
        hass: HomeAssistant,
        customer_type: str,
        region: str,
        location: str,
        entry_id: str,
        integration_name: str,
    ) -> None:
        self.hass = hass
        self.customer_type = customer_type
        self.region = region
        self.location = location
        self.entry_id = entry_id
        self.integration_name = integration_name

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(days=1),
        )

        # Lista sensorów (uzupełniana w async_setup_entry)
        self.sensors: List[SensorEntity] = []

        # Ścieżki do plików z lokalną kopią danych i ETagiem
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self._json_path = os.path.join(base_dir, f"pgk_slupsk_{entry_id}.json")
        self._etag_path = os.path.join(base_dir, f"pgk_slupsk_{entry_id}.etag")

        _LOGGER.debug(
            "PGKSlupskCoordinator init: type=%s, region=%s, location=%s, entry_id=%s, json_path=%s, etag_path=%s",
            self.customer_type,
            self.region,
            self.location,
            self.entry_id,
            self._json_path,
            self._etag_path,
        )

        # Harmonogram – zaplanowanie nocnego odświeżenia danych z API
        # Codziennie o północy losujemy moment w oknie 00:05–05:00 i planujemy retry_update_data().
        async_track_time_change(
            hass,
            self._schedule_random_night_refresh,
            hour=0,
            minute=0,
            second=0,
        )

        # Harmonogram – odświeżenie stanów wszystkich sensorów o 00:00:05 (bez API)
        async_track_time_change(
            hass,
            self._handle_sensors_midnight_refresh,
            hour=0,
            minute=0,
            second=5,
        )

    # -------------------------------------------------------------------------
    # Harmonogram – wersja oparta o helpery HA
    # -------------------------------------------------------------------------

    def _schedule_random_night_refresh(self, now: datetime) -> None:
        """Codziennie o północy zaplanuj losowe odświeżenie danych API
        w oknie godzinowym 00:00–05:59.
        """
        import random

        # losowy czas: godzina 0–4, minuta 0–59
        hour = random.randint(0, 4)
        minute = random.randint(0, 59)

        # planujemy na dzisiejszą noc – callback odpala się o 00:00
        target_time = now.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        # bezpieczeństwo: gdyby now > target_time (np. HA ruszył o 00:00:01)
        if target_time <= now:
            target_time += timedelta(days=1)

        delay = (target_time - now).total_seconds()

        _LOGGER.info(
            "PGK Słupsk – nocne sprawdzenie danych (API) zaplanowane na %s (za %.0f s)",
            target_time,
            delay,
        )

        def _do_refresh(_now: datetime) -> None:
            _LOGGER.debug("PGK Słupsk – rozpoczynamy automatyczne odświeżenie danych (API)")
            self.hass.loop.create_task(self.retry_update_data())

        # planujemy wywołanie na wyliczony moment
        async_call_later(self.hass, delay, _do_refresh)


    async def _handle_sensors_midnight_refresh(self, now: datetime) -> None:
        """Codziennie odśwież stany wszystkich sensorów tuż po północy (00:00:05),
        bez pobierania nowych danych z API – tylko przeliczenie stanu i atrybutów.
        """
        _LOGGER.debug(
            "PGK Słupsk – odświeżanie stanów sensorów (bez pobierania danych), liczba sensorów: %s",
            len(self.sensors),
        )

        for sensor in list(self.sensors):
            try:
                _LOGGER.debug(
                    "PGK Słupsk – odświeżanie sensora: %s",
                    getattr(sensor, "entity_id", sensor),
                )
                # 1) Zaktualizuj dane wewnętrzne sensora z koordynatora / czasu
                await sensor.async_update()
                # 2) Wyślij nowy stan + atrybuty do HA
                sensor.async_write_ha_state()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "PGK Słupsk – błąd podczas odświeżania sensora %s: %s",
                    getattr(sensor, "entity_id", sensor),
                    exc,
                )

    async def async_refresh_sensors(self) -> None:
        """Ręczne odświeżenie sensorów (ta sama logika co o północy)."""
        from datetime import datetime

        now = datetime.now()
        _LOGGER.debug(
            "PGK Słupsk – ręczne wywołanie odświeżenia sensorów (bez API), now=%s",
            now,
        )
        await self._handle_sensors_midnight_refresh(now)

    async def retry_update_data(self) -> None:
        """Ponawiaj próbę odświeżenia danych co 10 minut w przypadku niepowodzenia."""
        while True:
            try:
                await self.async_request_refresh()
                _LOGGER.info("PGK Słupsk - dane zostały pomyślnie odświeżone")
                break  # Wyjście z pętli po udanym odświeżeniu
            except UpdateFailed as e:
                _LOGGER.error(
                    "Błąd odświeżania danych: %s. Próba ponowienia za 10 minut.",
                    e,
                )
                await asyncio.sleep(RETRY_INTERVAL)

    # -------------------------------------------------------------------------
    # Obsługa plików (surowy JSON + ETag)
    # -------------------------------------------------------------------------

    async def _load_raw_json(self) -> Any | None:
        """Wczytaj surowy JSON z pliku (dokładnie to, co zwróciło API)."""

        def _load() -> Any | None:
            if not os.path.exists(self._json_path):
                return None
            try:
                with open(self._json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                _LOGGER.warning(
                    "Nie udało się odczytać lokalnego pliku JSON %s: %s",
                    self._json_path,
                    exc,
                )
                return None

        return await self.hass.async_add_executor_job(_load)

    async def _save_raw_json(self, data: Any) -> None:
        """Zapisz surowy JSON dokładnie tak, jak dostałeś go z API."""

        def _save() -> None:
            try:
                with open(self._json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except OSError as exc:
                _LOGGER.warning(
                    "Nie udało się zapisać lokalnego pliku JSON %s: %s",
                    self._json_path,
                    exc,
                )

        await self.hass.async_add_executor_job(_save)

    async def _load_etag(self) -> str | None:
        """Wczytaj ETag z pliku .etag."""

        def _load() -> str | None:
            if not os.path.exists(self._etag_path):
                return None
            try:
                with open(self._etag_path, "r", encoding="utf-8") as f:
                    etag = f.read().strip()
                    return etag or None
            except OSError as exc:
                _LOGGER.warning(
                    "Nie udało się odczytać pliku ETag %s: %s",
                    self._etag_path,
                    exc,
                )
                return None

        return await self.hass.async_add_executor_job(_load)

    async def _save_etag(self, etag: str | None) -> None:
        """Zapisz ETag do pliku .etag."""
        if not etag:
            return

        def _save() -> None:
            try:
                with open(self._etag_path, "w", encoding="utf-8") as f:
                    f.write(etag)
            except OSError as exc:
                _LOGGER.warning(
                    "Nie udało się zapisać pliku ETag %s: %s",
                    self._etag_path,
                    exc,
                )

        await self.hass.async_add_executor_job(_save)

    async def clear_cache(self) -> None:
        """Usuń lokalny cache (JSON + ETag) i wymuś pełne odświeżenie danych."""
        import os

        removed = []

        for path in (self._json_path, self._etag_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    removed.append(path)
            except OSError as exc:
                _LOGGER.warning("PGK Słupsk – nie udało się usunąć %s: %s", path, exc)

        if removed:
            _LOGGER.info("PGK Słupsk – usunięto cache: %s", removed)
        else:
            _LOGGER.info("PGK Słupsk – brak cache do usunięcia")

        # Po wyczyszczeniu pamięci podręcznej wymuszamy pełne pobranie z API
        await self.retry_update_data()


    # -------------------------------------------------------------------------
    # Główna aktualizacja
    # -------------------------------------------------------------------------

    async def _async_update_data(self) -> Dict[str, Dict[str, Any]]:
        """Pobierz dane z nowego API (RSC) albo użyj lokalnego JSON-a.

        Nowe API:
        * GET strony z nagłówkami RSC (Accept: text/x-component, RSC: 1)
        * wyciągamy scheduleData z payloadu (Flight)
        * konwertujemy do "starego" formatu JSON (lista obiektów)

        Cache:
        * zapisujemy legacy JSON do pgk_slupsk_<entry_id>.json
        * w razie błędu sieci/parsowania używamy lokalnego pliku (jeśli jest)
        """

        local_raw = await self._load_raw_json()
        session = async_get_clientsession(self.hass)

        url = _build_rsc_url(self.customer_type, self.region, self.location)
        headers: Dict[str, str] = {
            "Accept": "text/x-component",
            "RSC": "1",
            "User-Agent": "WebKit=Android",
            "Referer": f"{BASE_SITE}{PATH}",
        }

        raw_data: Any | None = None

        try:
            async with session.get(url, headers=headers, timeout=30) as response:
                status = response.status
                if status != 200:
                    raise UpdateFailed(f"Nieprawidłowy kod HTTP z PGK: {status}")

                rsc_text = await response.text()
                schedule_data = _extract_schedule_data_from_rsc(rsc_text)
                raw_data = _convert_schedule_to_legacy(schedule_data)

                # zapis legacy JSON do pliku
                await self._save_raw_json(raw_data)
                _LOGGER.info(
                    "PGK Słupsk - dane odświeżone (RSC) region='%s' location='%s' entries=%s",
                    self.region,
                    self.location,
                    len(raw_data) if isinstance(raw_data, list) else "?",
                )

        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UpdateFailed) as err:
            _LOGGER.error("Błąd podczas pobierania/parsu RSC PGK Słupsk: %s", err)
            if local_raw is not None:
                _LOGGER.warning(
                    "Używam lokalnego pliku JSON %s z powodu błędu API",
                    self._json_path,
                )
                raw_data = local_raw
            else:
                raise UpdateFailed(f"Błąd podczas aktualizacji danych: {err}") from err

        if raw_data is None:
            raise UpdateFailed("Brak danych surowych po próbie pobrania i wczytania z pliku")

        processed_data = self._process_raw_data(raw_data)

        _LOGGER.debug("Przetworzone dane z API: %s", processed_data)

        # Aktualizacja danych w koordynatorze – to automatycznie powiadomi encje
        self.async_set_updated_data(processed_data)

        return processed_data

    # -------------------------------------------------------------------------
    # Przetwarzanie surowego JSON-a z API / pliku
    # -------------------------------------------------------------------------

    def _process_raw_data(self, data: Any) -> Dict[str, Dict[str, Any]]:
        """Przekształć surowy JSON w strukturę używaną przez sensory.

        NIE ruszamy struktury surowego JSON-a w pliku – dalej zrzucamy tam dokładnie to,
        co przyszło z API. Tu tylko robimy słownik pod potrzeby sensorów.
        """
        processed_data: Dict[str, Dict[str, Any]] = {}

        if not isinstance(data, list):
            _LOGGER.warning(
                "Nieoczekiwany format danych PGK Słupsk (oczekiwano listy): %s",
                type(data),
            )
            return processed_data

        for entry in data:
            if not isinstance(entry, dict):
                continue

            try:
                waste_type_id = entry["TypOdpaduId"]
            except KeyError:
                _LOGGER.debug("Pominięto wpis bez TypOdpaduId: %s", entry)
                continue

            if waste_type_id not in processed_data:
                processed_data[waste_type_id] = {
                    "TypOdpadu": entry.get("TypOdpadu"),
                    "Kolor": entry.get("Kolor"),
                    "Daty": [],
                    "Updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

            # W oryginale: entry["Data"] – zostawiamy jak było
            dt = entry.get("Data")
            if isinstance(dt, str):
                processed_data[waste_type_id]["Daty"].append(dt)

        # Sortujemy daty i usuwamy duplikaty
        for wt_id, wt_data in processed_data.items():
            dates = sorted(set(wt_data.get("Daty", [])))
            wt_data["Daty"] = dates

        return processed_data


class PGKSlupskSensor(CoordinatorEntity, SensorEntity):
    """Sensor dla każdego typu odpadu."""
    _attr_translation_key = "waste_sensor"

    def __init__(
        self,
        coordinator: PGKSlupskCoordinator,
        waste_type_id: str,
        waste_data: Dict[str, Any],
        integration_name: str,
        hass: HomeAssistant,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._waste_type_id = waste_type_id
        self._waste_data = waste_data  # pełne dane z koordynatora
        self._integration_name = integration_name
        self._hass = hass
        self._entry_id = entry_id
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}::service")},
            name=DEVICE_NAME,
            manufacturer="PGK Słupsk",
            model=integration_name,
            entry_type=DeviceEntryType.SERVICE,
        )


        waste_info = WASTE_TYPES.get(waste_type_id, {})
        self._name = f"{waste_info.get('name', waste_data['TypOdpadu'])}"
        self._icon = waste_info.get("icon", "mdi:trash-can")
        self._color = waste_data["Kolor"]
        self._updated = waste_data["Updated"]

        # wszystkie daty z API -> od razu czyścimy do "od dziś wzwyż"
        today = datetime.now().date()
        cleaned_dates: list[str] = []
        for d_str in waste_data["Daty"]:
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d_obj >= today:
                cleaned_dates.append(d_str)
        self._dates = sorted(set(cleaned_dates))

        # znacznik ostatniego przeliczenia sensora (nie API)
        self._refreshed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Stabilne unique_id – NA TYM opiera się brak duplikatów przy reload
        self._unique_id = f"{entry_id}_waste_{waste_type_id}"

        self.entity_id = generate_entity_id(
            "sensor.{}",
            f"pgk_slupsk_{self._integration_name}_{self._name}",
            hass=self._hass,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    def _get_next_date(self):
        """Zwróć (najbliższa_data_datetime, days_diff) ignorując daty z przeszłości."""
        if not self._dates:
            return None, None

        today = datetime.now().date()
        candidates: List[date] = []

        for d_str in self._dates:
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d_obj >= today:
                candidates.append(d_obj)

        if not candidates:
            return None, None

        next_date = min(candidates)
        days_diff = (next_date - today).days
        return next_date, days_diff

    @property
    def state(self):
        """Zwróć stan sensora jako opis najbliższego odbioru."""
        next_date, days_diff = self._get_next_date()

        if next_date is not None and days_diff is not None:
            weekday_name = DAYS_TRANSLATION[next_date.weekday()]
            if days_diff == 0:
                _LOGGER.debug("Sensor %s - stan: dziś", self.name)
                return "dziś"
            if days_diff == 1:
                _LOGGER.debug("Sensor %s - stan: jutro", self.name)
                return "jutro"
            if days_diff == 2:
                _LOGGER.debug(
                    "Sensor %s - stan: %s, pojutrze", self.name, weekday_name
                )
                return f"{weekday_name}, pojutrze"
            if days_diff > 2:
                _LOGGER.debug(
                    "Sensor %s - stan: %s, za %s dni",
                    self.name,
                    weekday_name,
                    days_diff,
                )
                return f"{weekday_name}, za {days_diff} dni"

        _LOGGER.debug(
            "Sensor %s - stan: brak danych (brak przyszłych dat)", self.name
        )
        return "unknown"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Zwróć dodatkowe atrybuty sensora."""
        next_date, days_diff = self._get_next_date()
        next_date_str = next_date.strftime("%Y-%m-%d") if next_date is not None else None

        # Normalizacja nazwy typu odpadu
        t = self._waste_data["TypOdpadu"]
        self._waste_data["TypOdpadu"] = t[0].upper() + t[1:].lower()

        return {
            "Waste type": self._waste_data["TypOdpadu"],
            "Waste type (id)": self._waste_type_id,
            "Container color": self._color,
            # najbliższa przyszła / dzisiejsza data
            "Date": next_date_str,
            # lista wszystkich przyszłych (i dzisiejszej) dat – już oczyszczona w async_update
            "Dates": self._dates,
            "Days until pickup": days_diff,
            # czas ostatniego odświeżenia danych z API / pliku
            "Updated": self._updated,
            # czas ostatniego przeliczenia sensora (np. o 00:00:05)
            "Refreshed": self._refreshed,
        }

    @property
    def icon(self) -> str:
        return self._icon

    async def async_update(self) -> None:
        """Zaktualizuj dane sensora na podstawie danych z koordynatora.

        Nie wywołujemy tutaj ponownie odświeżania koordynatora,
        tylko czytamy to, co już zostało pobrane.
        """
        waste_data = self.coordinator.data.get(self._waste_type_id, {})

        if waste_data:
            # pełna synchronizacja z koordynatora
            self._waste_data = waste_data
            self._color = waste_data.get("Kolor", self._color)
            self._updated = waste_data.get("Updated", self._updated)

            # daty z koordynatora (albo zostajemy przy dotychczasowych, jeśli brak)
            raw_dates = waste_data.get("Daty", self._dates)

            # czyścimy: zostawiamy tylko dzisiejsze i przyszłe daty
            today = datetime.now().date()
            cleaned_dates: list[str] = []
            for d_str in raw_dates:
                try:
                    d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if d_obj >= today:
                    cleaned_dates.append(d_str)

            self._dates = sorted(set(cleaned_dates))

        # znacznik czasu – kiedy sensor został realnie przeliczony
        self._refreshed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class PGKSlupskDayBeforeSensor(CoordinatorEntity, SensorEntity):
    """Sensor dla odpadów, które mają być zabrane na następny dzień."""
    _attr_translation_key = "wtp_sensor"

    def __init__(
        self,
        coordinator: PGKSlupskCoordinator,
        integration_name: str,
        hass: HomeAssistant,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._integration_name = integration_name
        self._hass = hass
        self._entry_id = entry_id

        self._name = "Odpady do przygotowania"
        self.entity_id = generate_entity_id(
            "sensor.{}",
            f"pgk_slupsk_{integration_name}_wtp",
            hass=self._hass,
        )

        # Stabilne unique_id: jeden sensor "co jutro" na entry
        self._unique_id = f"{entry_id}_waste_tomorrow"
        self._refreshed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}::service")},
            name=DEVICE_NAME,
            manufacturer="PGK Słupsk",
            model=integration_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def state(self) -> str:
        """Zwróć listę typów odpadów do zabrania na następny dzień."""
        if not self.coordinator.data:
            return "none"

        next_day = datetime.now() + timedelta(days=1)
        waste_types_for_next_day: List[str] = []

        for waste_type_id, waste_data in self.coordinator.data.items():
            for waste_date in waste_data["Daty"]:
                waste_date_obj = datetime.strptime(waste_date, "%Y-%m-%d")
                if waste_date_obj.date() == next_day.date():
                    waste_info = WASTE_TYPES.get(waste_type_id, {})
                    waste_types_for_next_day.append(
                        waste_info.get("name", waste_data["TypOdpadu"])
                    )

        _LOGGER.debug(
            "Sensor Odpady do przygotowania - dane na jutro: %s",
            waste_types_for_next_day,
        )
        return ", ".join(waste_types_for_next_day) if waste_types_for_next_day else "brak"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Atrybuty pomocnicze."""
        return {
            "Refreshed": self._refreshed
        }

    @property
    def icon(self) -> str:
        return "mdi:calendar-check"

    async def async_update(self) -> None:
        """Aktualizuj stan sensora (logika stanu jest dynamiczna)."""
        self._refreshed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
