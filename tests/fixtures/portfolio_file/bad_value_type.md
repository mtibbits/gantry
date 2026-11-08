# Portfolio

A fictional portfolio file for tests.

## Business requirements

- BR-1: Keep the household running.
- BR-2: Ship the harbor rebuild.

## Settings

```toml
portfolio_version = 1

[site.harbor]
serves = "BR-1"

[capacity]
attention_hours = "all week"
agent_lanes = true
block_min_hours = true
block_default_hours = true
block_start_cost_hours = true
attention_mapping = true

[[capacity.reserved]]
day = 1
start = 9
hours = true
label = 1

[milestone]
project = 1
issue = 1
label = 1

[thresholds]
wip_max = true
unknown_deps_max = true
unplanned_max = true
share_gap_points = true
neglect_weeks = true
effort_window_weeks = true
```
