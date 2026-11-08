# Portfolio

A fictional portfolio file for tests.

## Business requirements

- BR-1: Keep the household running.

```markdown
## Example

- BR-9: An example, not a requirement.
```

- BR-2: Ship the harbor rebuild.

## Settings

```toml
# The settings.
## Not a heading: a TOML comment.
portfolio_version = 1

[capacity]
attention_hours = { mon = 4 }
agent_lanes = 1
## Still a comment.

[[allocation]]
from = 2026-01-05
shares = { harbor = 60, orchard = 40 }

[site.harbor]
serves = ["BR-1", "BR-2"]

[milestone]
project = "lighthouse"
issue = "Issue-5"
```
