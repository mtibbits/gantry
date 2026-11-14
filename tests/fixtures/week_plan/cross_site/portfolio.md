# Portfolio

A fictional portfolio file for the week planner's cross-site tests (#62): two sites whose leaves wait on each other, with room to spare every day.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 50 }

[capacity]
attention_hours = { mon = 6, tue = 6, wed = 6, thu = 6, fri = 6 }
agent_lanes = 12
block_min_hours = 1
block_start_cost_hours = 0.25
```
