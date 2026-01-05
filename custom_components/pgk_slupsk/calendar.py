"""Kalendarz dla harmonogramu odbioru odpadów PGK Słupsk."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, WASTE_TYPES, DEVICE_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Utwórz encję kalendarza dla danego wpisu konfiguracji."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    integration_name = entry.title or "PGK Słupsk"
    entry_id = entry.entry_id

    entity = PGKSlupskCalendar(
        coordinator=coordinator,
        integration_name=integration_name,
        hass=hass,
        entry_id=entry_id,
    )

    # async_add_entities([entity], update_before_add=True)
    async_add_entities([entity])
    # domyślnie update_before_add=False, więc nie prosi o dodatkowy refresh


class PGKSlupskCalendar(CoordinatorEntity, CalendarEntity):
    """Encja kalendarza pokazująca wszystkie odbiory odpadów dla danej lokalizacji."""

    def __init__(
        self,
        coordinator,
        integration_name: str,
        hass: HomeAssistant,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._integration_name = integration_name
        self._hass = hass
        self._entry_id = entry_id

        # Nazwa kalendarza w HA
        # self._name = f"{integration_name} - harmonogram odpadów"
        self._name = "Harmonogram odpadów"

        # Stabilne unique_id – jedno na wpis
        self._unique_id = f"{entry_id}_waste_calendar"

        # ID encji kalendarza
        self.entity_id = generate_entity_id(
            "calendar.{}",
            f"pgk_slupsk_{integration_name}_harmonogram",
            hass=self._hass,
        )

        # Bieżące najbliższe wydarzenie (HA używa tego np. w kartach)
        self._event: Optional[CalendarEvent] = None
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}::service")},
            name=DEVICE_NAME,
            manufacturer="PGK Słupsk",
            model=integration_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def icon(self):
        """Ikona kalendarza."""
        return "mdi:calendar"

    @property
    def name(self) -> str:
        """Nazwa encji kalendarza."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Unikalny identyfikator encji."""
        return self._unique_id

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Zwróć najbliższe (lub trwające) wydarzenie."""
        # Odświeżamy najbliższe zdarzenie na podstawie aktualnych danych
        self._event = self._compute_next_event()
        return self._event

    async def async_update(self) -> None:
        """Zaktualizuj dane kalendarza na podstawie danych z koordynatora.

        NIE wywołujemy tutaj odświeżania koordynatora – dane dostarcza
        DataUpdateCoordinator, a my tylko przeliczamy najbliższe wydarzenie.
        """
        self._event = self._compute_next_event()

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> List[CalendarEvent]:
        """Zwróć listę wydarzeń w zadanym przedziale czasu.

        Obsługuje zarówno wydarzenia datetime, jak i całodniowe (date).
        """
        events = self._generate_all_events()
        result: List[CalendarEvent] = []

        tz = dt_util.get_time_zone(self._hass.config.time_zone)

        def _as_dt(value):
            """Zamień date/datetime na datetime z prawidłową strefą."""
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    return value.replace(tzinfo=tz)
                return value
            if isinstance(value, date):
                # wydarzenie całodniowe → początek dnia
                return datetime.combine(value, time.min).replace(tzinfo=tz)
            return None

        for ev in events:
            if ev.end is None:
                continue

            ev_start = _as_dt(ev.start)
            ev_end = _as_dt(ev.end)
            if ev_start is None or ev_end is None:
                continue

            # klasyczne sprawdzenie nakładania się zakresów
            if ev_start < end_date and ev_end > start_date:
                result.append(ev)

        _LOGGER.debug(
            "Kalendarz PGK Słupsk: zwrócono %s wydarzeń w zakresie %s - %s",
            len(result),
            start_date,
            end_date,
        )
        return result

    # -------------------------------------------------------------------------
    # Pomocnicze: generowanie wydarzeń z danych koordynatora
    # -------------------------------------------------------------------------

    def _generate_all_events(self) -> List[CalendarEvent]:
        """Wygeneruj wszystkie wydarzenia na podstawie danych z koordynatora.

        Każdy wpis (typ odpadu, data) -> wydarzenie całodniowe:
        - tytuł: nazwa odpadu jak w sensorach,
        - start: data odbioru (all-day),
        - koniec: dzień po dacie odbioru (all-day, otwarty interval [start, end)).
        """
        events: List[CalendarEvent] = []

        data: Dict[int, Dict[str, Any]] = self.coordinator.data or {}
        if not data:
            return events

        for waste_type_id, waste_data in data.items():
            waste_info = WASTE_TYPES.get(waste_type_id, {})
            # dokładnie ta sama logika nazwy, co w sensorze:
            title = f"{waste_info.get('name', waste_data.get('TypOdpadu', 'Odpady'))}"

            for d_str in waste_data.get("Daty", []):
                try:
                    d_obj: date = datetime.strptime(d_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                # Wydarzenie całodniowe:
                # start = data odbioru, end = następny dzień
                start_dt: date = d_obj
                end_dt: date = d_obj + timedelta(days=1)

                event = CalendarEvent(
                    summary=title,
                    start=start_dt,
                    end=end_dt,
                    description=f"{self._integration_name}",
                )
                events.append(event)

        # Sortujemy po dacie początku
        events.sort(key=lambda ev: ev.start or dt_util.now())
        return events

    def _compute_next_event(self) -> Optional[CalendarEvent]:
        """Znajdź najbliższe nadchodzące LUB trwające wydarzenie.

        Obsługuje zarówno wydarzenia datetime, jak i całodniowe (date).
        Dzięki temu:
        - all-day event „dzisiaj” będzie traktowany jako trwający,
        - stan encji kalendarza będzie 'on' w trakcie trwania wydarzenia.
        """
        events = self._generate_all_events()
        if not events:
            return None

        tz = dt_util.get_time_zone(self._hass.config.time_zone)
        now = dt_util.now(tz)

        def _as_dt(value: Any) -> Optional[datetime]:
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    return value.replace(tzinfo=tz)
                return value
            if isinstance(value, date):
                return datetime.combine(value, time.min).replace(tzinfo=tz)
            return None

        # Przygotuj listę (ev, start, end) tylko dla wydarzeń, które jeszcze się nie skończyły
        prepared: List[tuple[CalendarEvent, datetime, datetime]] = []
        for ev in events:
            start_dt = _as_dt(ev.start)
            end_raw = ev.end or ev.start
            end_dt = _as_dt(end_raw)
            if start_dt is None or end_dt is None:
                continue
            # interesują nas tylko te, które kończą się po "now"
            if end_dt <= now:
                continue
            prepared.append((ev, start_dt, end_dt))

        if not prepared:
            return None

        # Najpierw spróbuj znaleźć wydarzenia trwające teraz
        ongoing = [item for item in prepared if item[1] <= now < item[2]]
        if ongoing:
            # Jeśli jakieś trwa, wybierz to, które zaczęło się najwcześniej
            ongoing_sorted = sorted(ongoing, key=lambda item: item[1])
            return ongoing_sorted[0][0]

        # Jeśli żadne nie trwa, wybierz najbliższe przyszłe (najmniejszy start)
        prepared_sorted = sorted(prepared, key=lambda item: item[1])
        return prepared_sorted[0][0]
