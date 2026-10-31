"""depends_on entries: classification (#8).

An entry with no `:` names an issue: id in this site's WBS.md. `<site>:<id>`,
both halves id tokens, names this site when `<site>` is exactly its name and
another site otherwise. Every other form is malformed.
"""

from gantry.model import DepRef
from gantry.sites import ID_TOKEN_RE


def classify(entry: str, site: str) -> DepRef:
    if ":" not in entry:
        return DepRef(entry, "local", site, entry)
    prefix, _, suffix = entry.partition(":")
    if ID_TOKEN_RE.fullmatch(prefix) and ID_TOKEN_RE.fullmatch(suffix):
        if prefix == site:
            return DepRef(entry, "local", site, suffix)
        return DepRef(entry, "foreign", prefix, suffix)
    return DepRef(entry, "malformed", None, None)
