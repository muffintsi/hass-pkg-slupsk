[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=pgk_slupsk)


Integracja z harmonogramem wywozu odpadów Przedsiębiorstwa Gospodarki Komunalnej Sp. z o.o. w Słupsku.

Pozwala na obsługę w Home Assistant harmonogramu wywozu odpadów miasta Słupsk i pozostałych miejscowości obsługiwanych przez PGK Słupsk.

- obsługa wielu lokalizacji
- automatyczna aktualizacja
- konfiguracja z GUI Home Assistant po instalacji komponentu ręcznie lub poprzez HACS
- osobny sensor dla każdego typu odpadu
- sensor specjalny zawierający listę odpadów do wystawienia na jutro (końcówka nazwy wtp)

Integration with the waste collection schedule of the Przedsiębiorstwa Gospodarki Komunalnej Sp. z o.o. in Słupsk.

It allows managing the waste collection schedule for the city of Słupsk and other localities served by PGK Słupsk in Home Assistant.

- Support for multiple locations
- Automatic updates
- Configuration via the Home Assistant GUI after installing the component manually or through HACS
- A separate sensor for each type of waste
- Special sensor containing a list of waste to be put out tomorrow (ending of name wtp)

Przykład karty z prezentacją danych:
Example of a card with data presentation:

https://github.com/thomasloven/lovelace-auto-entities

```
type: custom:auto-entities
card:
  type: entities
  title: Odpady
  state_color: true
  show_header_toggle: true
filter:
  include:
    - entity_id: /sensor.pgk_slupsk_miasto_slupsk_zabudowa_jednorodzinna_purpurowa/
  exclude:
    - state: /off/
    - state: /brak danych/
    - state: /unavailable/
    - entity_id: /sensor.pgk_slupsk_miasto_slupsk_zabudowa_jednorodzinna_purpurowa_wtp/
show_empty: false
sort:
  method: attribute
  attribute: Data
```
Przykład automatyzacji przypominającej o wystawieniu odpadow na jutro:
Example of automation reminding to put waste out for tomorrow:
```
alias: Trash - Wystaw odpady notify
description: ""
triggers:
  - trigger: time
    at: "20:00:00"
conditions:
  - condition: not
    conditions:
      - condition: state
        entity_id: sensor.pgk_slupsk_miasto_slupsk_zabudowa_jednorodzinna_purpurowa_wtp
        state: none
actions:
  - action: notify.signal
    data:
      message: >-
        Wystaw odpady. Jutro odbiór: {{
        states('sensor.pgk_slupsk_miasto_slupsk_zabudowa_jednorodzinna_purpurowa_wtp')|lower
        }}.
mode: single
```
