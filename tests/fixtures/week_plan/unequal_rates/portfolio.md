# Portfolio

A fictional portfolio file for the week planner's tests (#37): two share sites whose rated work runs at different rates.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 50 }

[capacity]
attention_hours = { mon = 4, tue = 4, wed = 4, thu = 4, fri = 4 }
agent_lanes = 8
block_min_hours = 1
block_start_cost_hours = 0.25
```
