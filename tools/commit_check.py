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
