# San Jose Water for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/anthonysecco/sjwater-stats)](https://github.com/anthonysecco/sjwater-stats/releases)
[![Validate](https://github.com/anthonysecco/sjwater-stats/actions/workflows/validate.yml/badge.svg)](https://github.com/anthonysecco/sjwater-stats/actions/workflows/validate.yml)
[![Tests](https://github.com/anthonysecco/sjwater-stats/actions/workflows/tests.yml/badge.svg)](https://github.com/anthonysecco/sjwater-stats/actions/workflows/tests.yml)

**See where your water goes and what it costs.** This integration reads your San Jose Water smart meter and bills, using your [SJWaterHUB](https://www.sjwaterhub.com) login. It does four things:

- **Energy dashboard, water:** puts your hourly water use in the Water section, with up to two years of history on first setup.
- **Energy dashboard, cost:** spreads each bill over the hours it covers, so the Water section shows dollars too.
- **Billing periods:** last period's use, the same period a year earlier and the change, plus use so far this period.
- **Leak alerts:** **Leak alert** turns on when San Jose Water flags a possible leak.

> **Unofficial.** This project is not affiliated with or endorsed by San Jose Water. SJWaterHUB has no public API, so this integration signs in the way the website does and reads the same data your browser shows. If San Jose Water changes the site, it can break until it's updated. Please [open an issue](https://github.com/anthonysecco/sjwater-stats/issues) if that happens.

## Contents
- [Before you start](#before-you-start)
- [Installation](#installation)
- [Setup](#setup)
- [Energy dashboard](#energy-dashboard)
- [Entities](#entities)
- [Leak detection](#leak-detection)
- [Options](#options)
- [Upgrading](#upgrading)
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

The first update imports all available hourly history (up to two years) and your billing history.

## Energy dashboard

**Settings → Dashboards → Energy → Water consumption → Add water source**, then:
1. Pick **San Jose Water usage (1234)**, where 1234 is the last four digits of your account.
2. Under cost, choose **Use an entity tracking the total costs** and pick **San Jose Water cost (1234)**.

Both are long-term statistics the integration writes directly, one value per hour: `sjwater_stats:<account>_water` in gallons (Home Assistant converts it to liters or m³ if you use metric) and `sjwater_stats:<account>_water_cost` in your currency.

**How cost is spread:** a bill is a fixed service charge plus a usage charge. The integration estimates the fixed part per day from your recent bills, so no rate tables are needed. Each bill's fixed part is spread evenly over its hours and the usage part in proportion to each hour's gallons.
- When the smart meter covers the whole billing period, the hours add up to the bill exactly.
- For the period the meter only partly covers (your first one), the covered hours get their share.
- **Hours after your last bill have no cost yet.** They fill in when the next bill appears, every two months.

> **Why not a sensor?** SJWaterHUB publishes each hour's reading 4–6 hours late, and bills weeks after the fact. A regular sensor would record that water and money at the time they *arrived*, which is the wrong hour. The imported statistics place every gallon and dollar in the hour it belongs to. The sensors below deliberately have no `state_class`, so Home Assistant doesn't build second, mistimed statistics from them.

## Entities

Entity IDs assume the default device name **San Jose Water**. Names follow "group, detail" (Account, Data, Last billing period, Leak, Usage), so related entities sort together.

### Usage (sensors, gallons)
| Entity | Meaning |
|---|---|
| `sensor.san_jose_water_usage_yesterday` | Yesterday's total (`complete` tells you whether all 24 hours are in). |
| `sensor.san_jose_water_usage_last_7_days` / `_last_30_days` | Rolling totals of complete days. |
| `sensor.san_jose_water_usage_average_daily` | 30-day daily average, your "normal". |
| `sensor.san_jose_water_usage_this_billing_period` | Smart-meter use since your last meter read (`period_start`, `days` attributes). |

### Last billing period
"Last billing period" is the latest period that has its bill. A new meter read is billed a few days later; until then these sensors stay on the previous period, so usage, cost and the comparison always describe the same period. The year-ago period is the one whose read date is closest to 12 months earlier.

| Entity | Meaning |
|---|---|
| `sensor.san_jose_water_last_billing_period_cost` | That period's bill, in USD. Attributes: `period_start`, `period_end`, `days`, `ccf`, `bill_date`, `bill_amount`. |
| `sensor.san_jose_water_last_billing_period_cost_year_ago` | The year-ago period's bill, same attributes. |
| `sensor.san_jose_water_last_billing_period_usage` | Gallons billed for that period, same attributes. |
| `sensor.san_jose_water_last_billing_period_usage_year_ago` | Gallons billed for the year-ago period, same attributes. |
| `sensor.san_jose_water_last_billing_period_vs_year_ago` | Change in average daily use between the two, in %. Per day, because periods differ by a few days; matches SJWaterHUB's "Compared to same period last year". Attributes give both periods' dates and gallons per day. |

### Leaks and diagnostics
| Entity | Meaning |
|---|---|
| `binary_sensor.san_jose_water_leak_alert` | **Leak alert:** San Jose Water flagged a possible leak in the last 48 published hours (see [leak detection](#leak-detection)). Device class *moisture*, so it works with leak cards and alerts. |
| `sensor.san_jose_water_leak_alert_last` | When San Jose Water last flagged a possible leak hour. |
| `binary_sensor.san_jose_water_data_delayed` | SJWaterHUB hasn't published new readings for 24 h (diagnostic). |
| `sensor.san_jose_water_data_updated` | When SJWaterHUB last published readings (diagnostic). |
| `sensor.san_jose_water_account_number` | Your San Jose Water account number (diagnostic). |

## Leak detection

The integration passes on San Jose Water's own leak analysis. SJWaterHUB marks hours it considers a possible leak (the orange **Possible Leak** bars under Understand Usage). **Leak alert** turns on while such an hour is within the last 48 published hours. Attributes:
- `portal_leak_hour`: the latest flagged hour.
- `leak_hours_last_7_days`: how many hours were flagged in the last week.

San Jose Water also says that leaks under about 90 gallons a day may not show up in meter data, so a creeping **Usage average daily** is still worth a look.

## Options

**Settings → Devices & services → San Jose Water → Configure.** Saving reloads the integration.

| Option | Default | Notes |
|---|---|---|
| Update interval | 6 h | 2–24 h. SJWaterHUB publishes hours a few hours late and bills every two months, so checking more often doesn't help. Each check signs in and makes about a dozen small requests, each with a 30 s timeout. |
| Data delayed after | 24 h | |

## Upgrading

**From 0.1:** 0.2 drops the hour-level sensors and the integration's own leak heuristics, because the portal publishes hours too late for them to be useful. The entities below are **removed automatically** on upgrade, so update any automations or cards that use them:
- `usage_today`, `last_hour_usage`, `flow_rate`, `peak_hourly_usage`, `overnight_minimum`, `continuous_use`, `yesterday_vs_average`
- `binary_sensor.…_high_daily_usage` and `binary_sensor.…_high_hourly_usage`

The matching options (high-use thresholds, continuous use, overnight window) are removed too. An update interval still on the old 2 h default moves to 6 h.

**From 0.2.0:** 0.2.1 renames entities so they sort into groups. The old ones are removed automatically on update:

| 0.2.0 | 0.2.1 |
|---|---|
| `binary_sensor.…_possible_leak` | `binary_sensor.…_leak_alert` |
| `sensor.…_last_leak_alert` | `sensor.…_leak_alert_last` |
| `sensor.…_average_daily_usage` | `sensor.…_usage_average_daily` |
| `sensor.…_last_bill` | `sensor.…_last_billing_period_cost` |
| `sensor.…_same_period_last_year_usage` | `sensor.…_last_billing_period_usage_year_ago` |
| `sensor.…_last_billing_period_vs_last_year` | `sensor.…_last_billing_period_vs_year_ago` |

New: `sensor.…_last_billing_period_cost_year_ago`.

## Examples

Ready-to-use automations and cards are in **[docs/automations.md](docs/automations.md)**:
- Leak alert to your phone
- Shutting the main valve
- New bill report, compared with last year
- Weekly water summary
- Dashboard cards

A quick one:
```yaml
alias: "Water: possible leak"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_leak_alert
    to: "on"
actions:
  - action: notify.notify
    data:
      title: "Possible water leak"
      message: >
        San Jose Water flagged a possible leak at
        {{ state_attr('binary_sensor.san_jose_water_leak_alert', 'portal_leak_hour') | as_datetime | as_local | as_timestamp | timestamp_custom('%a %-I %p') }}.
```

## Data, timing and privacy

- **Lag:** SJWaterHUB publishes readings about every 4–6 hours and bills every two months. Values are about the last published hours, not right now. The `data_through` attributes say how current they are.
- **Polling:** every 6 hours by default. Each check signs in, opens the usage page, fetches the hourly data, then opens Usage History and Billing History: about a dozen requests. If billing can't be read, the hourly data still updates and the last billing values are kept.
- **Daylight saving:** the portal labels hours in Pacific clock time. The integration converts them correctly, merging the nonexistent spring-forward hour into the next one.
- **Privacy:**
  - Your username and password are stored in Home Assistant's config entry, like any other integration, and are only sent to sjwaterhub.com.
  - Diagnostics downloads redact them and your account number.
  - The account number sensor shows your account number in Home Assistant; disable it if you share your dashboards.
  - The integration reads bill dates and amounts and meter reads. It doesn't read or store your name or address.
- **Tests** use synthetic data that only mimics the portal's format. No real customer data is in this repository.

## Troubleshooting

- **"Username or password not accepted":** check that you can sign in at sjwaterhub.com. Use your SJWaterHUB username, not your account number. The log line names the portal's `ErrorCode`; include it in an issue if a browser sign-in works but the integration's doesn't.
- **"No qualifying accounts":** SJWaterHUB accepted the login but lists no account for it. Check that the account appears on the website.
- **"Verification step":** sign in once in a browser and complete what it asks, then retry.
- **"No hourly smart-meter readings":** your meter hasn't been upgraded yet, or its data isn't connected. Check **Understand Usage** on the website.
- **Energy dashboard shows nothing for today:** that's expected until SJWaterHUB publishes the hours. Look again in a few hours.
- **No cost for recent days:** expected until the bill for that period appears.
- **Billing sensors are unknown:** the log says why ("billing history unavailable"). The hourly data keeps updating.
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

`tests/portal.py` is a synthetic SJWaterHUB. It produces the portal's exact response and page shapes, including daylight-saving days, zero-padded future hours and the billing pages, so the whole flow is tested without an account.

## License

[MIT](LICENSE).
