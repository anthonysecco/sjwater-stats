# Water-saving automations and cards

Ideas for using San Jose Water data to catch leaks early and keep usage in check. Replace `notify.notify`, `valve.*` and similar entities with your own. Paste an example into **Settings → Automations → ⋮ → Edit in YAML**.

> Readings arrive 4–6 hours late, so these automations react to the latest *published* hours. For instant shut-off, pair them with a local leak sensor or a smart valve that has its own flow sensing. This integration is the long-term early-warning layer.

- [Leak alert with the reason](#leak-alert-with-the-reason)
- [Close the main valve on a confirmed leak](#close-the-main-valve-on-a-confirmed-leak)
- [Morning report when yesterday was high](#morning-report-when-yesterday-was-high)
- [Hose or sprinkler left running](#hose-or-sprinkler-left-running)
- [Weekly water summary](#weekly-water-summary)
- [Irrigation sanity check](#irrigation-sanity-check)
- [Dashboard cards](#dashboard-cards)
- [Template ideas](#template-ideas)

## Leak alert with the reason

```yaml
alias: "Water: possible leak"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_possible_leak
    to: "on"
actions:
  - variables:
      reasons: "{{ state_attr('binary_sensor.san_jose_water_possible_leak', 'reasons') or [] }}"
  - action: notify.notify
    data:
      title: "💧 Possible water leak"
      message: >-
        {% set text = {
          'overnight_flow': 'water ran every hour overnight',
          'continuous_use': 'water has run for ' ~ states('sensor.san_jose_water_continuous_use') ~ ' hours straight',
          'portal_leak_flag': 'San Jose Water flagged a possible leak'
        } %}
        {% for r in reasons %}{{ text.get(r, r) | capitalize if loop.first else text.get(r, r) }}{{ '; ' if not loop.last }}{% endfor %}.
        Check toilets, faucets, irrigation valves and the water heater.
        Data through {{ state_attr('sensor.san_jose_water_usage_today', 'data_through') | as_datetime | as_local | as_timestamp | timestamp_custom('%a %-I %p') }}.
mode: single
```

**Find a silent leak yourself:** turn off every tap and appliance for two hours, ideally overnight. Then look at `sensor.san_jose_water_last_hour_usage` once those hours are published. Anything above 0 is water you didn't use on purpose.

## Close the main valve on a confirmed leak

Only act when two signals agree. Overnight flow and continuous use together are a strong sign.

```yaml
alias: "Water: close main valve on strong leak signal"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_possible_leak
    to: "on"
conditions:
  - condition: template
    value_template: >-
      {% set r = state_attr('binary_sensor.san_jose_water_possible_leak', 'reasons') or [] %}
      {{ 'overnight_flow' in r and 'continuous_use' in r }}
  - condition: state
    entity_id: group.family
    state: not_home
actions:
  - action: valve.close_valve
    target:
      entity_id: valve.main_water
  - action: notify.notify
    data:
      title: "Main water valve closed"
      message: "Continuous flow detected while nobody was home. Open it again from the app when it's checked."
mode: single
```

## Morning report when yesterday was high

```yaml
alias: "Water: high use yesterday"
triggers:
  - trigger: time
    at: "08:30:00"
conditions:
  - condition: state
    entity_id: binary_sensor.san_jose_water_high_daily_usage
    state: "on"
actions:
  - action: notify.notify
    data:
      title: "Water use was high yesterday"
      message: >-
        {{ states('sensor.san_jose_water_usage_yesterday') | int }} gal, which is
        {{ states('sensor.san_jose_water_yesterday_vs_average') | int }}% of your
        {{ states('sensor.san_jose_water_average_daily_usage') | int }} gal daily average.
        Biggest hour: {{ state_attr('binary_sensor.san_jose_water_high_hourly_usage', 'peak_gallons') | int(0) }} gal.
```

## Hose or sprinkler left running

```yaml
alias: "Water: very large hour"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_high_hourly_usage
    to: "on"
actions:
  - action: notify.notify
    data:
      title: "Big water hour"
      message: >-
        {{ state_attr('binary_sensor.san_jose_water_high_hourly_usage', 'peak_gallons') | int }} gal in one hour
        ({{ state_attr('binary_sensor.san_jose_water_high_hourly_usage', 'peak_hour') | as_datetime | as_local | as_timestamp | timestamp_custom('%a %-I %p') }}).
        A hose or sprinkler left on?
```

## Weekly water summary

```yaml
alias: "Water: weekly summary"
triggers:
  - trigger: time
    at: "09:00:00"
conditions:
  - condition: time
    weekday: [sun]
actions:
  - action: notify.notify
    data:
      title: "Your water week"
      message: >-
        Last 7 days: {{ states('sensor.san_jose_water_usage_last_7_days') | int }} gal
        ({{ (states('sensor.san_jose_water_usage_last_7_days') | float(0) / 7) | round(0) | int }} gal/day,
        30-day average {{ states('sensor.san_jose_water_average_daily_usage') | int }} gal/day).
        Overnight minimum last night: {{ states('sensor.san_jose_water_overnight_minimum') }} gal.
```

## Irrigation sanity check

If your irrigation controller is in Home Assistant, compare what it *should* have used with what the meter saw. This catches broken heads and stuck valves.

```yaml
alias: "Water: irrigation used more than expected"
triggers:
  - trigger: time
    at: "10:00:00"
conditions:
  # yesterday was a watering day for your controller (adapt to your integration)
  - condition: template
    value_template: "{{ states('sensor.irrigation_last_run') | as_datetime | as_local | as_timestamp | timestamp_custom('%F') == (now() - timedelta(days=1)).strftime('%F') }}"
  - condition: numeric_state
    entity_id: sensor.san_jose_water_yesterday_vs_average
    above: 250
actions:
  - action: notify.notify
    data:
      message: "Watering day used {{ states('sensor.san_jose_water_usage_yesterday') | int }} gal, much more than usual. Check for a broken sprinkler head or stuck valve."
```

## Dashboard cards

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: binary_sensor.san_jose_water_possible_leak
  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: sensor.san_jose_water_usage_yesterday
      - type: tile
        entity: sensor.san_jose_water_average_daily_usage
      - type: tile
        entity: sensor.san_jose_water_overnight_minimum
      - type: tile
        entity: sensor.san_jose_water_continuous_use
  - type: statistics-graph
    title: Water use, last 30 days
    chart_type: bar
    period: day
    days_to_show: 30
    stat_types: [change]
    entities:
      - entity: sjwater_stats:YOUR_ACCOUNT_water   # Developer tools → Statistics shows the exact ID
  - type: statistics-graph
    title: Hourly, last 2 days
    chart_type: bar
    period: hour
    days_to_show: 2
    stat_types: [change]
    entities:
      - entity: sjwater_stats:YOUR_ACCOUNT_water
```

The **Energy dashboard** (water section) also gives day, week, month and year views of the same statistic.

## Template ideas

Gallons per person per day (the U.S. average is about 80):
```jinja
{{ (states('sensor.san_jose_water_average_daily_usage') | float(0) / 3) | round(0) }}  {# 3 people #}
```

Projected bill-period use in CCF (San Jose Water bills about every 2 months, 1 CCF = 748 gal):
```jinja
{{ (states('sensor.san_jose_water_average_daily_usage') | float(0) * 60 / 748) | round(1) }}
```

Hours since the last reading:
```jinja
{{ ((now() - as_datetime(states('sensor.san_jose_water_data_updated'))).total_seconds() / 3600) | round(1) }}
```
