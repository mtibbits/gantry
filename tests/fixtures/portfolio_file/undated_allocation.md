# Portfolio

A fictional portfolio file for tests.

## Business requirements

- BR-1: Keep the household running.
- BR-2: Ship the harbor rebuild.

## Settings
```toml
portfolio_version = 1

[capacity]
attention_hours = { mon = 4 }
agent_lanes = 1

[[allocation]]
shares = { harbor = 50, orchard = 30, quarry = 20 }

[[allocation]]
from = "2026-01-05"
shares = { harbor = 50, orchard = 30, quarry = 20 }

[[allocation]]
from = 2026-01-05T09:00:00
shares = { harbor = 50, orchard = 30, quarry = 20 }
```
