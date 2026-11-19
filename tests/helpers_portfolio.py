"""devagent's portfolio JSON as test data (#83): the committed fixture and the gated sample document.

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). tools/sample_portfolio.py makes both
documents with the pinned devagent release's own aggregator.
"""

from pathlib import Path

import helpers_devagent as hd
import sample_portfolio as sp
from helpers_config import copy_samples

DOC_NAME = "portfolio.json"


written_doc = sp.written_doc


def build_sample_document(tmp, env=None, os_name=None):
    """(the document's path, the samples copy): devagent's portfolio JSON over a
    copy of samples/ under `tmp`, written by the checkout the gate admits.

    The gate comes first (tests/helpers_devagent.py: skip, warn or fail), so
    nothing is copied without a checkout.
    """
    checkout = hd.require_devagent(env, os_name)
    copy = copy_samples(Path(tmp))
    return sp.make_sample(checkout, copy), copy


def read_doc(tmp_path, doc):
    """read_portfolio over `doc` written as <tmp_path>/portfolio.json, with
    tmp_path the only root. `doc` is a parsed document, written in devagent's
    own form, or bytes, written as they are."""
    from gantry.portfolio_json import read_portfolio
    from gantry.roots import directory_roots

    path = Path(tmp_path) / DOC_NAME
    path.write_bytes(doc if isinstance(doc, bytes) else sp.dumps(doc))
    return read_portfolio(directory_roots([tmp_path]), path)
