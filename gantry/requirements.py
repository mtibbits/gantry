"""Requirements files: the eight layers, the ID grammar and the reader (#15).

A requirement ID is `<namespace>:<code>-<n>` (`gantry:FR-12`), a strict subset
of the `<namespace>:<id>` shape mtibbits/devagent#888 checks in `satisfies:`.
docs/requirements-format.md states the rules.
"""

import re
from typing import NamedTuple

# DEFAULT_BR_NAMESPACE is re-exported: its one home is gantry.config.
from gantry.config import DEFAULT_BR_NAMESPACE, ID_TOKEN_RE

# After Wiegers & Beatty: business requirement, business rule, use case,
# system requirement, functional requirement, quality attribute, external
# interface, constraint.
LAYERS = ("BR", "BRule", "UC", "SR", "FR", "QA", "EI", "CON")
PROJECT_LAYERS = frozenset(LAYERS) - {"BR"}  # how project files are read
MAX_DIGITS = 9  # operator decision Q1; as est_rules.NUM
_CODES = "|".join(sorted(LAYERS, key=len, reverse=True))
ID_RE = re.compile(
    rf"(?:(?P<namespace>{ID_TOKEN_RE.pattern}):)?"
    rf"(?P<code>{_CODES})-(?P<number>[1-9][0-9]{{0,{MAX_DIGITS - 1}}})"
)  # always fullmatch; case-sensitive


class ReqId(NamedTuple):
    namespace: str
    code: str
    number: int

    def __str__(self):
        return f"{self.namespace}:{self.code}-{self.number}"


def parse_id(text: str, default_namespace: str | None = None) -> ReqId | None:
    """The ReqId `text` names, or None. A bare ID takes `default_namespace`."""
    m = ID_RE.fullmatch(text)
    if m is None:
        return None
    namespace = m["namespace"]
    if namespace is None:
        if default_namespace is None or not ID_TOKEN_RE.fullmatch(default_namespace):
            return None
        namespace = default_namespace
    # At most MAX_DIGITS digits: int() never meets a long run.
    return ReqId(namespace, m["code"], int(m["number"]))
