"""The page-script harness for Gantry's generated pages (#53).

A plain module, not a test module, imported as page_script.harness. It imports
only the standard library and page_safety, and drives a Node child running
driver.js, which loads a page into jsdom. See docs/page-safety.md for the
protocol, the gate and the limits.
"""

import json
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

NODE_MISSING = "node is not on PATH"
JSDOM_MISSING = "jsdom is not installed: run npm ci --prefix tests/page_script"
JSDOM_STALE = "jsdom {found} is installed, but package.json pins {pinned}: run npm ci --prefix tests/page_script"


class HarnessError(Exception):
    """The harness itself failed: Node or jsdom not ready, the driver gone or silent."""


def find_node(env=None, which=None):
    """The node executable on env's PATH, or None."""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    # Never env.get("PATH"): a None path makes shutil.which read os.environ.
    return which("node", path=env.get("PATH", ""))


def _read_json(path):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise HarnessError(f"cannot read {path}: {exc}") from None


def node_status(env=None, which=None, root=None):
    """The reason Node tests cannot run, or None when Node and jsdom are ready."""
    root = HERE if root is None else root
    if find_node(env, which) is None:
        return NODE_MISSING
    installed = root / "node_modules" / "jsdom" / "package.json"
    if not installed.is_file():
        return JSDOM_MISSING
    package = _read_json(root / "package.json")
    pinned = package.get("dependencies", {}).get("jsdom") if isinstance(package, dict) else None
    if not isinstance(pinned, str):
        raise HarnessError(f"{root / 'package.json'} pins no jsdom version")
    found = _read_json(installed).get("version")
    if found != pinned:
        return JSDOM_STALE.format(found=found, pinned=pinned)
    return None
