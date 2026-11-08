# Portfolio

A fictional portfolio file for the sample worksites (#22). Gantry reads two
parts of it: the business requirements below and the settings block. The
rest, such as this paragraph, is the operator's own notes.

## Business requirements

- BR-1: Keep the harbor safe to work in, all year round.
- BR-2: Keep the market supplied every week.
- BR-3: Bring in a full harvest each autumn.

## Settings

```toml
# Shares are percentages of the operator's attention; each allocation sums to 100.
portfolio_version = 1

[[allocation]]
from = 2025-12-01
shares = { harbor = 40, orchard = 40, quarry = 20 }

[[allocation]]
from = 2026-01-05
shares = { harbor = 50, orchard = 30, quarry = 20 }

[[allocation]]
from = 2026-03-02
shares = { harbor = 30, orchard = 30, quarry = 40 }

[site.harbor]
serves = ["BR-1", "life:BR-2"]

[site.orchard]
serves = ["BR-3"]

[capacity]
attention_hours = { mon = 4, tue = 4, wed = 4, thu = 4, fri = 3, sat = 1, sun = 0 }
agent_lanes = 2
block_min_hours = 1
block_default_hours = 2
block_start_cost_hours = 0.25
attention_mapping = [0, 0.15, 0.3, 0.6, 1.0]

[[capacity.reserved]]
day = "wed"
start = "09:00"
hours = 2
label = "Standing meeting"

[milestone]
project = "lighthouse"
issue = "Issue-5"
label = "keeper's door repainted"
```
