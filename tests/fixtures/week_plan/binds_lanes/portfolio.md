# Portfolio

A fictional portfolio file for the week planner's tests (#61): agent lanes bind on the first day.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[capacity]
attention_hours = { mon = 8, tue = 8, wed = 8, thu = 8, fri = 8 }
agent_lanes = 1
block_min_hours = 1
block_start_cost_hours = 0.25
```
