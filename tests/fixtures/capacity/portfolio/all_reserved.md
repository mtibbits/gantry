# Portfolio

A fictional portfolio file for the week planner's tests of declared capacity (#39): reserved blocks take every weekday's attention hours; Wednesday's take more than it has.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 30, c = 20 }

[capacity]
attention_hours = { mon = 4, wed = 2, fri = 1 }
agent_lanes = 2
block_min_hours = 1
block_start_cost_hours = 0.25

[[capacity.reserved]]
day = "mon"
start = "09:00"
hours = 4

[[capacity.reserved]]
day = "wed"
start = "09:00"
hours = 2.5

[[capacity.reserved]]
day = "fri"
start = "09:00"
hours = 1
```
