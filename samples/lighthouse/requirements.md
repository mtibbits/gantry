# Lighthouse requirements

A fictional site's requirements, written in Gantry's requirements format.
Every name and fact here is made up.

## Use cases

- UC-1: A keeper logs the lamp's nightly check. {needs: [FR-1, QA-1]}
- UC-2: A visitor reads the week's checks.

## System requirements

- SR-1: The system keeps a log of every lamp check.

## Functional requirements

- FR-1: A keeper records a check with its time and the lamp's state. {refines: SR-1}
- FR-2: The log lists the checks of the last thirty nights,
  newest first.

## Quality attributes

- QA-1: Recording a check takes under a minute.

## Constraints

- CON-1: The log works without a network connection.

## An example, not a requirement

```text
- FR-9: a line inside a fence is never read
```
