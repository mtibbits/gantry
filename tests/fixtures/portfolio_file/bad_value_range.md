# Portfolio

A fictional portfolio file for tests.

## Business requirements

- BR-1: Keep the household running.
- BR-2: Ship the harbor rebuild.

## Settings

```toml
portfolio_version = 1

[capacity]
attention_hours = { mon = 25, tue = 4 }
agent_lanes = -1
block_min_hours = 0
block_default_hours = 0.5
block_start_cost_hours = -0.5

[[capacity.reserved]]
day = "monday"
start = "24:00"
hours = 0

[thresholds]
wip_max = -1
unknown_deps_max = -1
unplanned_max = -2
share_gap_points = 0
neglect_weeks = 0
effort_window_weeks = 0
```
