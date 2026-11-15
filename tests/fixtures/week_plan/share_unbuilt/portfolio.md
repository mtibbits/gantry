# Portfolio

A fictional portfolio file for the week planner's tests (#37): shares name projects that are not built sites.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 10, b = 20, zz = 70 }

[[allocation]]
from = 2026-01-26
shares = { zz = 60, yy = 40 }

[capacity]
attention_hours = { mon = 6, tue = 6, wed = 6, thu = 6, fri = 6 }
agent_lanes = 2
block_min_hours = 1
block_start_cost_hours = 0.25
```
