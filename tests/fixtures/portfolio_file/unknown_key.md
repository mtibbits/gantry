# Portfolio

A fictional portfolio file for tests.

## Business requirements

- BR-1: Keep the household running.
- BR-2: Ship the harbor rebuild.

## Settings

```toml
portfolio_version = 1
colour = "blue"

[capacity]
attention_hours = { mon = 4 }
agent_lanes = 1
agent_lane = 2

[site.harbor]
serves = ["BR-1"]
serve = ["BR-2"]
```
