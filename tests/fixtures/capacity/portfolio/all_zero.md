# Portfolio

A fictional portfolio file for the week planner's tests of declared capacity (#39): its attention_hours table gives every weekday 0.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 30, c = 20 }

[capacity]
attention_hours = { mon = 0, tue = 0, wed = 0, thu = 0, fri = 0 }
agent_lanes = 2
block_min_hours = 1
block_start_cost_hours = 0.25
```
