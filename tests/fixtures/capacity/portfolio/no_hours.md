# Portfolio

A fictional portfolio file for the week planner's tests of declared capacity (#39): it sets no attention_hours.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 30, c = 20 }

[capacity]
agent_lanes = 2
block_min_hours = 1
block_start_cost_hours = 0.25
```
