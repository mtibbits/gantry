# The week plan

The week plan will fit each site's open leaves into the operator's week:
the agent hours a leaf takes, and the operator hours it needs while it
runs. The plan itself is #61, and the week view that shows it is #38.
Neither exists yet. This page states the part that does: each leaf's
attention need and rate (`gantry/attention.py`, #36).

## Attention need

A leaf's **attention need** is how much of the operator it needs while an
agent works on it. Its **rate** is the operator hours it needs for each
agent hour: a leaf of 6 agent hours at a rate of 0.25 needs 1.5 operator
hours. Every leaf gets a rate from the first of four sources that applies,
tagged with that source and a basis naming its inputs.

No page shows needs, rates, tags or bases yet; the week view (#38) will.
Today only a bad `h:` reaches a page, as a report entry.

### The scale

The need is a whole number from 0 to 4. 0 means the leaf runs without the
operator; 4 means it needs them throughout. 1 to 3 lie between, and the
mapping below turns each need into a rate.

The meaning of `h:` is Gantry's: this page defines it. devagent's open
contract for the key (mtibbits/devagent#888) fixes only its shape, as a
`WBS.md` key it knows.

### `h:`

`h:` is the operator's own score of a leaf's need, written in its
`WBS.md` metadata. It is one ASCII digit from 0 to 4, written as a scalar:
`h: 3`, `h: "3"` and `h: '3'` are the same score. Anything else is not
used, and the leaf's need comes from its next source:

- a digit outside 0 to 4 (`h: 5`, `h: -1`), a decimal (`h: 2.5`) or a word
  (`h: high`);
- a sign or a leading zero (`h: +3`, `h: 03`), an empty value, or a space
  inside the quotes (`h: "3 "`);
- a digit that is not ASCII (a fullwidth 3);
- a list (`h: [3]`, `h: [3, 4]`), or a value still quoted after its quotes
  are read (`h: "'3'"`).

The value is read through the same metadata reader as every other key
(`meta_values`, which #12 replaces), so Gantry has no quote rule of its own.

Every `h:` that is not used is a `bad_h` entry in the site's report, listed
in the worksite page's "missing or unreadable" panel
([worksite-page.md](worksite-page.md#the-report)). The entry names the
leaf's key and the value as written. `h:` belongs on a leaf: on a row with
children it is not used either, and its entry says "on a row with
children, not a leaf". Every node is checked whatever its state, so a bad
`h:` on a done or skipped leaf is reported too, though only open leaves are
planned.

An `h:` value reaches a page only as plain text, through the page's safe
render path: in the leaf's metadata in its details, and in a `bad_h`
message.

### The heuristic

A leaf with no usable `h:` and no site ratio gets an **estimated** need:

- 1 to start;
- plus 1 when its checking tier is the top tier, `fable` (exactly: `opus`,
  `inherit` and `Fable` are not);
- plus 1 when its flags name `research` or `spike`, as a whole word in any
  case (`researcher` does not count); both words still add 1;
- plus 1 when it is its epic's first open leaf, a fresh context: it has an
  epic, no leaf of that epic is done, and it is the epic's first open leaf
  in `WBS.md` order. A skipped leaf is not work done;
- capped at 4.

The basis names each step that applied, in that order: for example
"1 to start; checking tier fable; flagged research and spike; first open
leaf of its epic". A leaf with neither model tiers nor flags takes 1, plus
the first-leaf step when it applies, and its basis adds
"no model tiers or flags recorded".

The heuristic never gives 0: Gantry does not assume a leaf runs without the
operator unless the operator scores it so.

Model tiers come from the leaf's recorded tiers, wherever they were read:
the estimates file today, and whatever source #14 gives them. Flags come only from the
estimates file, and only while #14 keeps it as their source: a leaf with no
estimates-file row has no flags.

### The mapping

The mapping turns needs 0 to 4 into rates. Gantry's default is
`0, 0.1, 0.25, 0.5, 1.0` operator hours per agent hour, so need 0 costs
nothing and need 4 costs one operator hour for every agent hour.

The portfolio file's `capacity.attention_mapping`
([portfolio-file.md](portfolio-file.md)) replaces the default for the whole
installation. The portfolio file's own check refuses a mapping that is not
five numbers, each 0 or more, not decreasing. Gantry then refuses one that
holds a value that is not finite or is above its limit. That limit,
`MAX_RATE`, is 24 operator hours per agent hour, which keeps a plan's
arithmetic finite; no real rate comes near it. In each case the default is in use, with
one of these reasons:

- "the portfolio file's attention_mapping was rejected (see the portfolio
  file's report)": the portfolio file's report holds a `bad_value` entry
  for the key;
- "the portfolio file's attention_mapping holds a value that is not
  finite";
- "the portfolio file's attention_mapping holds a value above 24".

A portfolio file that does not set the key gives the default with no
reason. So does one whose settings cannot be read at all; the portfolio
file's own report says why. The mapping in use, its source (`default` or
`portfolio_file`) and any reason are kept together, so the week view can
show them.

### The four sources

A leaf's rate comes from the first of these that applies:

1. `scored`: its `h:` score through the mapping;
2. `measured`: its site's measured ratio, when one is passed;
3. `declared`: its site's declared ratio, when one is passed;
4. `estimated`: its heuristic need through the mapping.

A scored or estimated value keeps its need beside its rate; a measured or
declared value has a rate only. Every value carries a basis led by its
tag: "scored: h: 3", "measured: site ratio 0.3; 12 closed issues",
"declared: site ratio 0.2", "estimated: 1 to start; …". An `h: 0` is a
score like any other, and wins over both site ratios.

A **site ratio** is one rate for every leaf of a site. The two are separate
inputs, `measured=` and `declared=`: #40 will pass the ratio measured from
devagent's history, and #39 the ratio devagent's capacity declaration
states. No caller passes either yet, so today every leaf is scored or
estimated. A ratio passed in the other source's slot is refused
(`ValueError`), so no caller can invert the ranking.

A site ratio must be a number from 0 to `MAX_RATE`. One that is not is
refused when it is built (`ValueError`), naming the reason: not a number (a
boolean is not one), not finite, negative, or above 24. 0 is a valid site
ratio: a site that runs without the operator. The same rule checks the
mapping's values.

### The site ratio source

A site's **site ratio source** is `measured` when a measured ratio is
passed, else `declared` when a declared one is, else `estimated`. It
depends on the site alone: no leaf's `h:` changes it.
