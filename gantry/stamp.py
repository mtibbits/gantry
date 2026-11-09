"""The build stamp and the sample-mode test (#11).

Every page and the index carry a stamp: when they were built and the as-of
date. In sample mode the stamp names only the as-of date, so two builds of
the same sample data write the same bytes. Sample mode holds when every root
of the run resolves inside the sample directory, `samples/` under the gantry
root: `outside(config_roots(config), sample_dir())` is empty, the one
definition #59's confinement uses too. A copy of the samples anywhere else is
never sample mode. No flag or config key switches it on.
docs/worksite-page.md states the stamp.
"""

from datetime import datetime, timezone

from gantry.roots import CONFIG_KEY, outside, sample_dir
from gantry.sites import config_roots, shown

SAMPLE_STAMP = "sample data as of {date}"
BUILT_STAMP = "built {when}; data as of {date}"


def _label(root) -> str:
    """How a reason names a root: by its key or file kind, never its path."""
    if root.key == CONFIG_KEY:
        return "the Gantry config"
    if root.key == "devagent_config":
        return "the devagent config"
    if root.key == "devdoc_dir":
        return f"devdoc_dir of {shown(root.site)}"
    return root.key


def public_text(text: str, roots) -> str:
    """`text` with each root's path, as configured and as resolved, replaced
    by its label, the longest path first."""
    pairs = {}
    for root in roots.roots:
        for path in (str(root.given), str(root.path)):
            pairs.setdefault(path, _label(root))
    for path in sorted(pairs, key=len, reverse=True):
        text = text.replace(path, pairs[path])
    return text


def sample_mode(config, *, samples=None, roots=None) -> bool:
    """Whether every root of the run resolves inside the sample directory."""
    if samples is None:
        # The module global at call time, so a test can patch it.
        samples = sample_dir()
    if samples is None:
        return False
    if roots is None:
        roots = config_roots(config)
    return not outside(roots, samples)


def stamp_text(config, *, now=None, samples=None, roots=None) -> str:
    """The stamp every page and the index carry; `now` must be timezone-aware.
    `roots` is the run's, when the caller has built them already."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("the build time must be timezone-aware")
    date = config.as_of.isoformat()
    if sample_mode(config, samples=samples, roots=roots):
        return SAMPLE_STAMP.format(date=date)
    when = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return BUILT_STAMP.format(when=when, date=date)
