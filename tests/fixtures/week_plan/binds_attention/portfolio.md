# Portfolio

A fictional portfolio file for the week planner's tests (#61): operator attention hours bind on the first day.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[capacity]
attention_hours = { mon = 2, tue = 2, wed = 2, thu = 2, fri = 2 }
agent_lanes = 2
block_min_hours = 1
block_start_cost_hours = 0.25
```
