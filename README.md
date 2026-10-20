# San Jose Water for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/anthonysecco/sjwater-stats)](https://github.com/anthonysecco/sjwater-stats/releases)
[![Validate](https://github.com/anthonysecco/sjwater-stats/actions/workflows/validate.yml/badge.svg)](https://github.com/anthonysecco/sjwater-stats/actions/workflows/validate.yml)
[![Tests](https://github.com/anthonysecco/sjwater-stats/actions/workflows/tests.yml/badge.svg)](https://github.com/anthonysecco/sjwater-stats/actions/workflows/tests.yml)

**See where your water goes, and catch leaks and runaway use early.** This integration reads the hourly readings from your San Jose Water smart meter, using your [SJWaterHUB](https://www.sjwaterhub.com) login. It does three things:

- **Energy dashboard:** puts your hourly water use in the Water section, with up to two years of history on first setup.
- **Leak alerts:** **Possible leak** turns on for water flowing all night, water that never stops, or a leak flag from San Jose Water itself.
- **Unusual use alerts:** **High daily usage** and **High hourly usage** turn on for days well above your normal, or a single hour that looks like a hose or sprinkler left running.

> **Unofficial.** This project is not affiliated with or endorsed by San Jose Water. SJWaterHUB has no public API, so this integration signs in the way the website does and reads the same data your browser shows. If San Jose Water changes the site, it can break until it's updated. Please [open an issue](https://github.com/anthonysecco/sjwater-stats/issues) if that happens.

## Contents
- [Before you start](#before-you-start)
- [Installation](#installation)
- [Setup](#setup)
- [Energy dashboard](#energy-dashboard)
- [Entities](#entities)
- [How leaks and unusual use are detected](#how-leaks-and-unusual-use-are-detected)
- [Options](#options)
- [Examples](#examples)
- [Data, timing and privacy](#data-timing-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Before you start

- **A San Jose Water account with a smart meter.** San Jose Water has been installing smart meters across its service area (San Jose, Cupertino, Campbell, Saratoga, Los Gatos and nearby). If you can see hourly bars under **Usage Insight → Understand Usage** on [sjwaterhub.com](https://www.sjwaterhub.com), this integration will work.
- **An SJWaterHUB login.** You create one on sjwaterhub.com with your account number.
- **Home Assistant 2026.3 or newer.**

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=anthonysecco&repository=sjwater-stats&category=integration)

Or add it by hand:
1. In Home Assistant open **HACS**, then the **⋮** menu (top right), then **Custom repositories**.
2. Repository: `https://github.com/anthonysecco/sjwater-stats`. Type: **Integration**. Click **Add**.
3. Search HACS for **San Jose Water**, open it and click **Download**.
4. **Restart Home Assistant.**

### Manual
Copy `custom_components/sjwater_stats` from the [latest release](https://github.com/anthonysecco/sjwater-stats/releases) into `config/custom_components/`, then restart.

## Setup

[![Open your Home Assistant instance and start setting up San Jose Water.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sjwater_stats)

**Settings → Devices & services → Add integration → San Jose Water.** Enter your SJWaterHUB username and password.
- If your login has **several accounts**, you'll be asked to pick one. Add the integration again for each of the others.
- If SJWaterHUB wants a **verification code, email confirmation or password change**, setup tells you so. Sign in once at sjwaterhub.com in a browser, then try again.
- If your password changes later, Home Assistant asks you to sign in again (**Repairs** / **Reconfigure**).

The first update imports all available hourly history, up to two years.

## Energy dashboard

**Settings → Dashboards → Energy → Water consumption → Add water source**, then pick **San Jose Water usage (1234)**, where 1234 is the last four digits of your account.

That entry is a long-term statistic the integration writes directly (`sjwater_stats:<account>_water`), one value per hour, in gallons. Home Assistant converts it to liters or m³ if you use metric.

> **Why not a sensor?** SJWaterHUB publishes each hour's reading 4–6 hours late. A regular sensor would record that water at the time it *arrived*, which is the wrong hour. The imported statistic places every gallon in the hour it was actually used, and it's updated in place when the portal revises recent hours. The sensors below deliberately have no `state_class`, so Home Assistant doesn't build a second, mistimed statistic from them.

To add a cost, use the Energy dashboard's **Use a static price** with your $/gallon. San Jose Water bills per CCF, and 1 CCF is 748 gallons. Divide your $/CCF rate by 748.

## Entities

Entity IDs assume the default device name **San Jose Water**.

### Alerts (binary sensors)
| Entity | Turns on when |
|---|---|
| `binary_sensor.san_jose_water_possible_leak` | Any leak signal is present (see [detection](#how-leaks-and-unusual-use-are-detected)). The `reasons` attribute lists which one. Device class *moisture*, so it works with leak cards and alerts. |
| `binary_sensor.san_jose_water_high_daily_usage` | Yesterday used ≥ 200 % of your 30-day daily average (and at least 50 gal). |
| `binary_sensor.san_jose_water_high_hourly_usage` | Any hour in the last 24 published hours used ≥ 400 gal. |
| `binary_sensor.san_jose_water_data_delayed` | SJWaterHUB hasn't published new readings for 24 h (diagnostic). |

### Usage (sensors, gallons)
| Entity | Meaning |
|---|---|
| `sensor.san_jose_water_usage_today` | Today so far, through the latest published hour (`data_through` attribute). |
| `sensor.san_jose_water_usage_yesterday` | Yesterday's total (`complete` tells you whether all 24 hours are in). |
| `sensor.san_jose_water_usage_last_7_days` / `_last_30_days` | Rolling totals of complete days. |
| `sensor.san_jose_water_average_daily_usage` | 30-day daily average, your "normal". |
| `sensor.san_jose_water_yesterday_vs_average` | Yesterday as a % of that average. |
| `sensor.san_jose_water_last_hour_usage` | The most recent published hour (`hour_start` attribute). |
| `sensor.san_jose_water_flow_rate` | That hour's average flow, in gal/min. **Not live:** the meter reports hourly and the data lags 4–6 h. |
| `sensor.san_jose_water_peak_hourly_usage` | Largest hour in the last 24 published hours. |
| `sensor.san_jose_water_overnight_minimum` | Lowest hour in last night's quiet window (default 1–4 AM). **0 is what you want.** |
| `sensor.san_jose_water_continuous_use` | Longest run of back-to-back hours with any use in the last 24 h. |
| `sensor.san_jose_water_last_leak_alert` | When San Jose Water last flagged a possible leak hour. |
| `sensor.san_jose_water_data_updated` | When SJWaterHUB last published readings (diagnostic). |

## How leaks and unusual use are detected

Most homes have hours with **no water use at all**, especially overnight. A leak, a running toilet or a stuck irrigation valve removes those quiet hours. **Possible leak** turns on for any of these:

| Reason (`reasons` attribute) | Meaning |
|---|---|
| `overnight_flow` | Every hour of last night's quiet window (default 1:00–4:00 AM) used water. |
| `continuous_use` | Water was used in at least 12 consecutive hours. |
| `portal_leak_flag` | San Jose Water's own analytics marked an hour in the last 48 h as a possible leak. |

**Unusual use:**
- **High daily usage** compares yesterday with your own 30-day average. It adapts to your household and season.
- **High hourly usage** catches a single big hour, such as a hose or sprinkler left on. Those typically run 300–600 gal/h.

The meter counts in steps of one cubic foot (7.48 gal), so a very slow drip can show up as an occasional 7-gallon hour rather than a constant flow. A falling **overnight minimum** or a creeping **average daily usage** are early hints worth watching.

## Options

**Settings → Devices & services → San Jose Water → Configure.** Saving reloads the integration.

| Option | Default | Notes |
|---|---|---|
| Update interval | 2 h | 1–6 h. SJWaterHUB adds readings every few hours, so checking more often doesn't help. Each check signs in and makes about 6 small requests, each with a 30 s timeout. |
| High daily use threshold | 200 % | Of your 30-day average. |
| High hourly use threshold | 400 gal | Raise it if your irrigation legitimately uses more in an hour. |
| Continuous use threshold | 12 h | |
| Overnight check starts / ends | 1 / 4 | Hours 0–23, end exclusive. **Move this window if your sprinklers run at night**, or set it to when your home is reliably idle. |
| Data delayed after | 24 h | |

## Examples

Ready-to-use automations and cards are in **[docs/automations.md](docs/automations.md)**:
- Leak alert to your phone
- Shutting the main valve
- Morning high-use report
- Weekly water summary
- Irrigation sanity check
- Dashboard cards

A quick one:
```yaml
alias: "Water: possible leak"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_possible_leak
    to: "on"
actions:
  - action: notify.notify
    data:
      title: "Possible water leak"
      message: >
        {{ state_attr('binary_sensor.san_jose_water_possible_leak', 'reasons') | join(', ') | replace('_', ' ') }}.
        Overnight minimum {{ states('sensor.san_jose_water_overnight_minimum') }} gal,
        {{ states('sensor.san_jose_water_continuous_use') }} h of continuous use.
```

## Data, timing and privacy

- **Lag:** SJWaterHUB publishes readings about every 4–6 hours. Alerts are about the last published hours, not right now. The `data_through` attributes say how current they are.
- **Polling:** every 2 hours by default. Each check signs in, opens the usage page and fetches the hourly data, about 6 requests.
- **Daylight saving:** the portal labels hours in Pacific clock time. The integration converts them correctly, merging the nonexistent spring-forward hour into the next one.
- **Privacy:**
  - Your username and password are stored in Home Assistant's config entry, like any other integration, and are only sent to sjwaterhub.com.
  - Diagnostics downloads redact them and your account number.
  - The integration doesn't read or store your name, address or billing details.
- **Tests** use synthetic data that only mimics the portal's format. No real customer data is in this repository.

## Troubleshooting

- **"Username or password not accepted":** check that you can sign in at sjwaterhub.com. Use your SJWaterHUB username, not your account number.
- **"Verification step":** sign in once in a browser and complete what it asks, then retry.
- **"No hourly smart-meter readings":** your meter hasn't been upgraded yet, or its data isn't connected. Check **Understand Usage** on the website.
- **Energy dashboard shows nothing for today:** that's expected until SJWaterHUB publishes the hours. Look again in a few hours.
- **Debug logging:**
  ```yaml
  logger:
    logs:
      custom_components.sjwater_stats: debug
  ```
  Then download diagnostics (**⋮ → Download diagnostics**) and attach both to an [issue](https://github.com/anthonysecco/sjwater-stats/issues). Logs never include your password.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check .
pytest
```

`tests/portal.py` is a synthetic SJWaterHUB. It produces the portal's exact response shapes, including daylight-saving days and zero-padded future hours, so the whole flow is tested without an account.

## License

[MIT](LICENSE).
