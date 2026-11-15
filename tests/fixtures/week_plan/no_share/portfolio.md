# Portfolio

A fictional portfolio file for the week planner's tests (#37): c and d have no share above 0; c is first in config order.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 100, d = 0 }

[capacity]
attention_hours = { mon = 6, tue = 6, wed = 6, thu = 6, fri = 6 }
agent_lanes = 1
block_min_hours = 1
block_start_cost_hours = 0.25
```
