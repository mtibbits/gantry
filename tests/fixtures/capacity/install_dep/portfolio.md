# Portfolio

A fictional portfolio file for the week planner's tests of a paused site (#39): three sites, each holding a share.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 30, c = 20 }

[capacity]
attention_hours = { mon = 6, tue = 6, wed = 6, thu = 6, fri = 6 }
agent_lanes = 2
block_min_hours = 1
block_start_cost_hours = 0.25
```
