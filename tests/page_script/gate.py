"""The Node gate for the page-script tests (#53).

A Node test skips when Node or jsdom is not ready, and fails instead when CI
is set, so a CI run can never pass on skipped page-script tests.
"""

import os

import pytest

from page_script.harness import node_status

CI_SUFFIX = " (CI is set, so a Node test fails instead of skipping)"


def require_node(env=None, which=None, root=None):
    """Return when Node and jsdom are ready; else skip, or fail under CI."""
    env = os.environ if env is None else env
    reason = node_status(env, which, root)
    if reason is None:
        return
    if env.get("CI"):
        pytest.fail(reason + CI_SUFFIX, pytrace=False)
    pytest.skip(reason)
