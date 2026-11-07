"""The build stamp and the sample-mode test (#11).

Every page and the index carry a stamp: when they were built and the as-of
date. In sample mode the stamp names only the as-of date, so two builds of
the same sample data write the same bytes. Sample mode holds when every root
the config names resolves inside the sample directory, `samples/` in the
Gantry source tree the running package was loaded from; an installation with
no source tree is never in sample mode. No flag or config key switches it on.

The names are #59's (`config_roots`, `outside`, `sample_dir`), so its swap
changes no caller; #59 drops `config_roots`' `sites` argument.
docs/worksite-page.md states the stamp.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from gantry.sites import shown

SAMPLE_MARKER = "gantry.sample.toml"
SAMPLE_STAMP = "sample data as of {date}"
BUILT_STAMP = "built {when}; data as of {date}"


def sample_dir(package_dir=None) -> Path | None:
    """<gantry package parent>/samples when it holds the sample config, else None."""
    package = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
    folder = package.parent / "samples"
    return folder if (folder / SAMPLE_MARKER).is_file() else None


def config_roots(config, sites) -> list[tuple[str, Path]]:
    """(label, path) for each file or folder the config names, labelled by its key or kind."""
    roots = [("the Gantry config", config.path), ("the devagent config", config.devagent_config),
             ("output_dir", config.output_dir)]
    for key in ("portfolio_file", "fires_list"):
        if getattr(config, key) is not None:
            roots.append((key, getattr(config, key)))
    for name, override in config.sites.items():
        for key in ("requirements", "estimates"):
            value = getattr(override, key)
            if isinstance(value, Path):
                roots.append((f"site.{name}.{key}", value))
    roots += [(f"devdoc_dir of {shown(site.name)}", site.planning_dir) for site in sites
              if site.planning_dir is not None]
    return roots


def public_text(text: str, roots) -> str:
    """`text` with each root's path replaced by its label, the longest path first."""
    for label, path in sorted(roots, key=lambda root: len(str(root[1])), reverse=True):
        text = text.replace(str(path), label)
    return text


def _canonical(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def outside(roots, base) -> list[Path]:
    """The roots' paths that are not `base` or inside it, both sides canonicalised."""
    inner = _canonical(base)
    found = []
    for _, path in roots:
        try:
            inside = os.path.commonpath([inner, _canonical(path)]) == inner
        except ValueError:
            # Another drive, or one path relative: never inside.
            inside = False
        if not inside:
            found.append(path)
    return found


def sample_mode(config, sites, *, samples=None) -> bool:
    """Whether every configured root resolves inside the sample directory."""
    if samples is None:
        # The module global at call time, so a test can patch it.
        samples = sample_dir()
    if samples is None:
        return False
    return not outside(config_roots(config, sites), samples)


def stamp_text(config, sites, *, now=None, samples=None) -> str:
    """The stamp every page and the index carry; `now` must be timezone-aware."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("the build time must be timezone-aware")
    date = config.as_of.isoformat()
    if sample_mode(config, sites, samples=samples):
        return SAMPLE_STAMP.format(date=date)
    when = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return BUILT_STAMP.format(when=when, date=date)
