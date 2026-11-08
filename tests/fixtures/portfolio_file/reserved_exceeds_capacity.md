# Portfolio

A fictional portfolio file for tests.

## Business requirements

- BR-1: Keep the household running.
- BR-2: Ship the harbor rebuild.

## Settings

```toml
portfolio_version = 1

[capacity]
attention_hours = { mon = 4, tue = 6 }
agent_lanes = 1

[[capacity.reserved]]
day = "mon"
start = "09:00"
hours = 3

[[capacity.reserved]]
day = "mon"
start = "13:00"
hours = 2
label = "Standing meeting"

[[capacity.reserved]]
day = "tue"
start = "09:00"
hours = 2
```
