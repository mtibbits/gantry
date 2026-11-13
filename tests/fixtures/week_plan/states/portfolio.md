# Portfolio

A fictional portfolio file for the week planner's tests (#61): two sites with leaves in every open state.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[capacity]
attention_hours = { mon = 6, tue = 6, wed = 6, thu = 6, fri = 6 }
agent_lanes = 3
block_min_hours = 1
block_start_cost_hours = 0.25
```
