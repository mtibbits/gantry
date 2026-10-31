"""Commit check against private data (#44).

One standard-library tool holds every rule, and every mode routes through the
same functions:

1. generated output, anywhere in the tree;
2. installation-config files outside samples/ and self-contained fixtures;
3. absolute local paths, in files and in commit messages;
4. the ignore rules.

`names` checks names and hostnames against a private denylist kept outside the
repo, and reports a count only. Content is read from git objects, never from
the work tree. See docs/commit-check.md.

The pattern literals below are assembled from pieces, so this file passes its
own rule 3.
"""

import re
from dataclasses import dataclass

SEP = "/"
BACK = "\\"
HOME_ROOTS = ("home", "Users", "root", "mnt")

# Start of line, or any character except a letter, digit, _ . - / ~ or colon.
BOUND = r"(?<![A-Za-z0-9_.\-" + SEP + "~:])"
PATH_FORMS = {
    "drive_backslash": r"(?<![A-Za-z0-9])[A-Za-z]:" + BACK * 2,
    "drive_slash": r"(?<![A-Za-z0-9])[A-Za-z]:" + SEP,
    "unc": BOUND + "(?:" + BACK * 4 + "|" + SEP * 2 + r")[A-Za-z0-9._$-]+[" + BACK * 2 + SEP + "]",
    "posix_home": BOUND + SEP + "(?:" + "|".join(HOME_ROOTS) + ")" + SEP,
    "msys_drive": BOUND + SEP + "[A-Za-z]" + SEP,
    "tilde": BOUND + "~(?:[A-Za-z][A-Za-z0-9._-]*)?" + SEP,
}
PATH_FORM_RES = {form: re.compile(pattern) for form, pattern in PATH_FORMS.items()}


def path_form_hits(text):
    """[(form, matched text)] for every absolute-path form in text."""
    return [
        (form, m.group(0))
        for form, pattern in PATH_FORM_RES.items()
        for m in pattern.finditer(text)
    ]


def line_hits(text):
    """[(line number, form, start, end)], lines numbered as git numbers them."""
    hits = []
    for lineno, line in enumerate(text.split("\n"), 1):
        for form, pattern in PATH_FORM_RES.items():
            hits += [(lineno, form, m.start(), m.end()) for m in pattern.finditer(line)]
    return hits


# --- the exemption list ----------------------------------------------------------

EXEMPT_PATH = "tools/commit_check_exempt.txt"
# Rules 1 and 2 have no exemption by design, and rule 4 is repo-level.
EXEMPTIBLE_RULES = {"absolute-path"}
FIELD_SEP = " | "


@dataclass(frozen=True)
class Exemption:
    path: str
    rule: str
    text: str
    reason: str
    lineno: int


def parse_exemptions(text):
    """(entries, errors) for the exemption list; errors are (line number, why)."""
    entries, errors = [], []
    for lineno, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [f.strip() for f in line.split(FIELD_SEP, 3)]
        if len(fields) != 4 or not all(fields):
            errors.append((lineno, "needs four fields: path | rule | text | reason"))
        elif fields[1] not in EXEMPTIBLE_RULES:
            errors.append((lineno, "only absolute-path can be exempted"))
        elif fields[0] == EXEMPT_PATH:
            errors.append((lineno, "the exemption list cannot exempt itself"))
        else:
            entries.append(Exemption(*fields, lineno))
    return entries, errors


def is_exempt(path, line, start, end, entries):
    """True when an entry for path has an occurrence of its text spanning the hit."""
    for entry in entries:
        if entry.path != path:
            continue
        at = line.find(entry.text)
        while at != -1:
            if at <= start and end <= at + len(entry.text):
                return True
            at = line.find(entry.text, at + 1)
    return False


def entry_is_live(entry, file_text):
    """True when some line holds the entry's text with a rule-3 hit inside it."""
    for line in file_text.split("\n"):
        if entry.text not in line:
            continue
        for _, _, start, end in line_hits(line):
            if is_exempt(entry.path, line, start, end, [entry]):
                return True
    return False
