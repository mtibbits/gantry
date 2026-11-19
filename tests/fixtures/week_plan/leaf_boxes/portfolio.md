# Portfolio

A fictional portfolio file for the week view's leaf box tests (#170): one leaf of each box kind, all placed
in the 10 days.

## Business requirements

- BR-1: Keep the fixture small enough to check by hand.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { a = 50, b = 50 }

[capacity]
attention_hours = { mon = 8, tue = 8, wed = 8, thu = 8, fri = 8 }
agent_lanes = 4
block_min_hours = 1
block_start_cost_hours = 0.25
```
