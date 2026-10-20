# Water-saving automations and cards

Ideas for using San Jose Water data to catch leaks and keep usage and cost in check. Replace `notify.notify`, `valve.*` and similar entities with your own. Paste an example into **Settings → Automations → ⋮ → Edit in YAML**.

> Readings arrive 4–6 hours late and bills every two months, so these automations react to the latest *published* data. For instant shut-off, pair them with a local leak sensor or a smart valve that has its own flow sensing. This integration is the long-term early-warning layer.

- [Leak alert](#leak-alert)
- [Close the main valve on a leak while away](#close-the-main-valve-on-a-leak-while-away)
- [New bill report](#new-bill-report)
- [Weekly water summary](#weekly-water-summary)
- [Irrigation sanity check](#irrigation-sanity-check)
- [Dashboard cards](#dashboard-cards)
- [Template ideas](#template-ideas)

## Leak alert

```yaml
alias: "Water: possible leak"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_leak_alert
    to: "on"
actions:
  - action: notify.notify
    data:
      title: "💧 Possible water leak"
      message: >-
        San Jose Water flagged a possible leak at
        {{ state_attr('binary_sensor.san_jose_water_leak_alert', 'portal_leak_hour') | as_datetime | as_local | as_timestamp | timestamp_custom('%a %-I %p') }}
        ({{ state_attr('binary_sensor.san_jose_water_leak_alert', 'leak_hours_last_7_days') }} flagged hours this week).
        Check toilets, faucets, irrigation valves and the water heater.
mode: single
```

**Find a silent leak yourself:** turn off every tap and appliance for a few hours, ideally overnight. Then look at those hours under **Understand Usage** on sjwaterhub.com, or in the hourly statistics graph below, once they're published. Anything above 0 is water you didn't use on purpose.

## Close the main valve on a leak while away

```yaml
alias: "Water: close main valve on leak while away"
triggers:
  - trigger: state
    entity_id: binary_sensor.san_jose_water_leak_alert
    to: "on"
conditions:
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
      message: "San Jose Water flagged a possible leak while nobody was home. Open it again from the app when it's checked."
mode: single
```

## New bill report

Runs when a new bill appears (every two months) and compares the period with a year earlier.

```yaml
alias: "Water: new bill"
triggers:
  - trigger: state
    entity_id: sensor.san_jose_water_last_billing_period_cost
conditions:
  - condition: template
    value_template: "{{ trigger.from_state.state not in ['unknown', 'unavailable'] and trigger.to_state.state not in ['unknown', 'unavailable'] }}"
actions:
  - action: notify.notify
    data:
      title: "New water bill: ${{ states('sensor.san_jose_water_last_billing_period_cost') }}"
      message: >-
        {% set p = 'sensor.san_jose_water_last_billing_period_usage' %}
        {{ states(p) | int }} gal over {{ state_attr(p, 'days') }} days
        ({{ state_attr(p, 'period_start') }} to {{ state_attr(p, 'period_end') }}).
        {% set change = states('sensor.san_jose_water_last_billing_period_vs_year_ago') | float(none) %}
        {% if change is not none %}{{ change | abs | round(0) | int }}% {{ 'more' if change > 0 else 'less' }} per day than the same period last year.{% endif %}
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
        30-day average {{ states('sensor.san_jose_water_usage_average_daily') | int }} gal/day).
        This billing period so far: {{ states('sensor.san_jose_water_usage_this_billing_period') | int }} gal
        in {{ state_attr('sensor.san_jose_water_usage_this_billing_period', 'days') }} days.
```

## Irrigation sanity check

If your irrigation controller is in Home Assistant, compare a watering day with your normal. This catches broken heads and stuck valves.

```yaml
alias: "Water: irrigation used more than expected"
triggers:
  - trigger: time
    at: "10:00:00"
conditions:
  # yesterday was a watering day for your controller (adapt to your integration)
  - condition: template
    value_template: "{{ states('sensor.irrigation_last_run') | as_datetime | as_local | as_timestamp | timestamp_custom('%F') == (now() - timedelta(days=1)).strftime('%F') }}"
  - condition: template
    value_template: >-
      {{ states('sensor.san_jose_water_usage_yesterday') | float(0)
         > 4 * states('sensor.san_jose_water_usage_average_daily') | float(0) }}
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
    entity: binary_sensor.san_jose_water_leak_alert
  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: sensor.san_jose_water_usage_yesterday
      - type: tile
        entity: sensor.san_jose_water_usage_average_daily
      - type: tile
        entity: sensor.san_jose_water_usage_this_billing_period
      - type: tile
        entity: sensor.san_jose_water_last_billing_period_vs_year_ago
      - type: tile
        entity: sensor.san_jose_water_last_billing_period_cost
      - type: tile
        entity: sensor.san_jose_water_last_billing_period_usage_year_ago
  - type: statistics-graph
    title: Water use, last 30 days
    chart_type: bar
    period: day
    days_to_show: 30
    stat_types: [change]
    entities:
      - entity: sjwater_stats:YOUR_ACCOUNT_water   # Developer tools → Statistics shows the exact ID
  - type: statistics-graph
    title: Water cost by month
    chart_type: bar
    period: month
    days_to_show: 365
    stat_types: [change]
    entities:
      - entity: sjwater_stats:YOUR_ACCOUNT_water_cost
  - type: statistics-graph
    title: Hourly, last 2 days
    chart_type: bar
    period: hour
    days_to_show: 2
    stat_types: [change]
    entities:
      - entity: sjwater_stats:YOUR_ACCOUNT_water
```

The **Energy dashboard** (water section) also gives day, week, month and year views of use and cost.

## Template ideas

Gallons per person per day (the U.S. average is about 80):
```jinja
{{ (states('sensor.san_jose_water_usage_average_daily') | float(0) / 3) | round(0) }}  {# 3 people #}
```

Projected use for this billing period in CCF (periods are about 60 days, 1 CCF = 748 gal):
```jinja
{% set s = 'sensor.san_jose_water_usage_this_billing_period' %}
{{ (states(s) | float(0) / (state_attr(s, 'days') | float(1)) * 60 / 748) | round(1) }}
```

Hours since the last reading:
```jinja
{{ ((now() - as_datetime(states('sensor.san_jose_water_data_updated'))).total_seconds() / 3600) | round(1) }}
```
