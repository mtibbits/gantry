"""The build stamp and the sample-mode test (#11).

Every page and the index carry a stamp: when they were built, the as-of
date and, since the build reads devagent's portfolio JSON (#12), when
devagent wrote it (its `generated_at`). In sample mode the stamp names only
the as-of date, so two builds of the same sample data write the same bytes. Sample mode holds when every root
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
# Appended to BUILT_STAMP (#12): a stale document builds with exit 0, and this line is what shows it.
DATA_WRITTEN = "; devagent data written {when}"
UTC_MINUTES = "%Y-%m-%d %H:%M UTC"


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


def _aware(when, what):
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError(f"the {what} must be timezone-aware")
    return when.astimezone(timezone.utc).strftime(UTC_MINUTES)


def stamp_text(config, *, generated_at, now=None, samples=None, roots=None) -> str:
    """The stamp every page and the index carry. `generated_at` is the portfolio JSON's
    (required: a caller cannot leave the data time out by default); it and `now` must be
    timezone-aware. `roots` is the run's, when the caller has built them already."""
    if now is None:
        now = datetime.now(timezone.utc)
    when, written = _aware(now, "build time"), _aware(generated_at, "document's generation time")
    date = config.as_of.isoformat()
    if sample_mode(config, samples=samples, roots=roots):
        return SAMPLE_STAMP.format(date=date)
    return BUILT_STAMP.format(when=when, date=date) + DATA_WRITTEN.format(when=written)
