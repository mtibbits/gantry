"""devagent's portfolio JSON as test data (#83): the committed fixture and the gated sample document.

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). tools/sample_portfolio.py makes both
documents with the pinned devagent release's own aggregator.
"""

import json
from pathlib import Path

import helpers_devagent as hd
import sample_portfolio as sp
from helpers_config import copy_samples


def written_doc():
    """tests/fixtures/portfolio/written.json, parsed: a fresh copy on every call, so a test may change it."""
    return json.loads(sp.WRITTEN.read_bytes().decode("utf-8"))


def build_sample_document(tmp, env=None, os_name=None):
    """(the document's path, the samples copy): devagent's portfolio JSON over a
    copy of samples/ under `tmp`, written by the checkout the gate admits.

    The gate comes first (tests/helpers_devagent.py: skip, warn or fail), so
    nothing is copied without a checkout.
    """
    checkout = hd.require_devagent(env, os_name)
    copy = copy_samples(Path(tmp))
    return sp.make_sample(checkout, copy), copy
