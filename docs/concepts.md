# Gantry concepts

This page explains the ideas Gantry is built on, not its features: the
[README](../README.md#what-it-shows) lists which views exist, and each view's
own document says what it shows.

## Worksites and the crane

Gantry treats each project as a worksite with its own tower crane. The
crane stands for the agents doing the work: they can lift only work whose
predecessors are finished, and only as much at once as the site has lanes.
A worksite page shows a site's work as a dependency network, with its
longest chain of waits, and a schedule of what is lifted when.

## Installations and the installation config

Gantry is one public tool with many private installations. An
**installation** is one private copy of Gantry with its own data. Its
**installation config** is one file that says where that data lives: the
planning folders to read and the folder to write pages to. The config is kept
outside the repository and named on every run, so no run falls back to a
default location ([config.md](config.md)).

## Sites come from devagent

The sites are the projects in devagent's own config, in the order that file
lists them. Gantry keeps no list of its own, so a project added to devagent
appears in Gantry with no other change.

## Leaves, estimates and the default unit

A project's work breakdown (`WBS.md`) is a tree. A **leaf** is a line with no
children, usually one tracker issue, and leaves are what get scheduled. A
leaf may carry an estimate of the agent hours it takes. A leaf with none is
**unestimated**: it is scheduled at the installation's default unit
(`default_unit_hours`, 6 unless the config says otherwise), and every figure
built on it says how many unestimated leaves it includes. Missing data is
shown, never hidden.

## Two schedules

Gantry keeps two schedules, and they answer different questions. The
worksite schedule spreads a site's leaves across agent lanes, so it models
agent capacity only and ignores the operator. The **week plan** is the plan:
it fits every site's leaves into the operator's week, and it models the
operator's attention, the hours a person must give a leaf while it runs.

## Fires

A **fire** is something that needs the operator now, whatever the plan says.
Fires come first, ahead of the plan, on the views made for the operator's
day.

## Requirements and the trace

Requirements sit in layers, after Wiegers and Beatty: business
requirements, business rules, use cases, system requirements, functional
requirements, quality attributes, external interfaces and constraints. A
leaf can name the requirements it satisfies, and the **requirements trace**
shows that link as an overlay on worksite pages, so work with no reason and
requirements with no work both show.

## The as-of date and sample mode

Every page is built as of a date, which the build treats as today. In
**sample mode**, when every input of a build sits inside the repository's
`samples/` folder, the as-of date is pinned by the sample config and each
page says it shows sample data as of that date, so two builds write the same
bytes. A copy of the samples anywhere else is not sample mode.

## Two scarce resources

Planning across many projects is limited by two things. **Agent capacity**
is how much work the agents can carry at once. **The operator's attention**
is how many hours a person can give to steering, reviewing and deciding.
The worksite schedule spends the first; the week plan spends both, and is
the only one that spends the second.

## Records already kept

Every number Gantry shows comes from records already kept: the work
breakdowns devagent maintains, their history in each planning repository,
and the few files an installation's config names, such as the portfolio
file of priorities and capacity. Gantry derives; it does not ask anyone to
re-enter what those records already say.

## Where the ideas come from

- *Getting Things Done*, by David Allen: capture everything, and decide the
  next action.
- *Making Work Visible*, by Dominica DeGrandis: show the work, and the work
  that is waiting.
- *The DevOps Handbook*, by Gene Kim, Jez Humble, Patrick Debois and John
  Willis: flow, feedback and small batches.
- *Software Requirements*, by Karl Wiegers and Joy Beatty: requirements in
  layers, traced to the work.
