"""The page-script harness for Gantry's generated pages (#53).

A plain module, not a test module, imported as page_script.harness. It imports
only the standard library and page_safety, and drives a Node child running
driver.js, which loads a page into jsdom. See docs/page-safety.md for the
protocol, the gate and the limits.
"""

import atexit
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import page_safety

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "driver.js"

NODE_MISSING = "node is not on PATH"
JSDOM_MISSING = "jsdom is not installed: run npm ci --prefix tests/page_script"
JSDOM_STALE = "jsdom {found} is installed, but package.json pins {pinned}: run npm ci --prefix tests/page_script"

# Seconds to wait for one reply; read at call time.
REPLY_TIMEOUT = 60
# Seconds to wait for a new driver to load jsdom, kept apart so a short
# REPLY_TIMEOUT times the page, not the load.
START_TIMEOUT = 120
CLOSE_WAIT = 5
# The driver's environment never takes these from ours.
DROPPED_ENV = ("NODE_OPTIONS", "NODE_PATH")


class PageScriptError(Exception):
    """The page failed: a script error, a jsdom gap, or a missing control or value."""


class HarnessError(Exception):
    """The harness itself failed: Node or jsdom not ready, the driver gone or silent."""


class Element(NamedTuple):
    tag: str
    attrs: dict
    classes: list
    text: str


class Planted(NamedTuple):
    created: list
    violations: list
    unrendered: list


# --- the gate's status ------------------------------------------------------------


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


# --- the driver process -------------------------------------------------------------


def _strings(args, message):
    if not all(isinstance(a, str) for a in args):
        raise TypeError(message)


class _Driver:
    """One Node child running driver.js; one request in flight at a time."""

    def __init__(self):
        reason = node_status()
        if reason is not None:
            raise HarnessError(reason)
        env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
        self._stderr = tempfile.TemporaryFile()
        try:
            # node itself, never the npm shim.
            self._proc = subprocess.Popen(
                [find_node(), str(DRIVER)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, env=env,
            )
        except OSError as exc:
            self._stderr.close()
            raise HarnessError(f"cannot start the page-script driver: {exc}") from None
        self._replies = queue.Queue()
        self.dead = False
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        # driver.js answers once jsdom has loaded.
        self._request({"op": "version"}, START_TIMEOUT)

    def _read(self):
        # Binary readline splits on b"\n" only; never splitlines(), which
        # also breaks on U+2028 inside a reply.
        for line in iter(self._proc.stdout.readline, b""):
            self._replies.put(line)
        self._replies.put(None)

    def _stderr_text(self):
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""
        return f"; its stderr: {text[-2000:]}" if text else ""

    def _fail(self, message):
        """Kill the driver and raise HarnessError: it cannot be trusted again."""
        self.kill()
        stderr = self._stderr_text()
        self._close_files()
        raise HarnessError(message + stderr)

    def _request(self, request, timeout=None):
        if self.dead:
            raise HarnessError("the page-script driver exited")
        line = json.dumps(request, ensure_ascii=True, allow_nan=False) + "\n"
        try:
            self._proc.stdin.write(line.encode("ascii"))
            self._proc.stdin.flush()
        except OSError:
            self._fail("the page-script driver exited")
        timeout = REPLY_TIMEOUT if timeout is None else timeout
        try:
            raw = self._replies.get(timeout=timeout)
        except queue.Empty:
            self._fail(f"no reply from the page-script driver within {timeout} s")
        if raw is None:
            self._fail("the page-script driver exited")
        try:
            reply = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            self._fail(f"page-script driver protocol error: {exc}")
        if isinstance(reply, dict) and reply.get("ok") is True and "result" in reply:
            return reply["result"]
        if isinstance(reply, dict) and reply.get("ok") is False and isinstance(reply.get("error"), str):
            if reply.get("kind") == "page":
                raise PageScriptError(reply["error"])
            if reply.get("kind") == "harness":
                raise HarnessError(reply["error"])
        self._fail(f"page-script driver protocol error: reply {raw[:200]!r}")

    def kill(self):
        """Stop the driver outright."""
        self.dead = True
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()

    def stop(self):
        """Ask the driver to close, wait, then kill it."""
        if self.dead:
            self._close_files()
            return
        self.dead = True
        try:
            self._proc.stdin.write(b'{"op": "close"}\n')
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(CLOSE_WAIT)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._close_files()

    def _close_files(self):
        # The reader ends at EOF; let it finish before its pipe is closed.
        self._reader.join(CLOSE_WAIT)
        for f in (self._proc.stdin, self._proc.stdout, self._stderr):
            try:
                f.close()
            except OSError:
                pass


class Page(_Driver):
    """A page loaded into jsdom by open_page."""

    def query(self, selector):
        """The elements matching a CSS selector, in document order."""
        found = self._request({"op": "query", "selector": selector})
        return [Element(e["tag"], dict(e["attrs"]), e["classes"], e["text"]) for e in found]

    def planted_nodes(self, planted, rules=None):
        """What the planted values became in the live document, and its rule breaks."""
        if isinstance(planted, str):
            raise TypeError("planted must be a collection of strings, not one string")
        planted = list(planted)
        for p in planted:
            if not isinstance(p, str):
                raise TypeError(f"planted item {p!r} is not a string")
        # page_safety.RULES is read at call time, never bound as a default.
        checked = page_safety.check_rules(page_safety.RULES if rules is None else rules)
        result = self._request({
            "op": "planted",
            "planted": planted,
            "rules": {key: sorted(items) for key, items in checked.items()},
        })
        return Planted(result["created"], result["violations"], result["unrendered"])

    def drive(self, control, value):
        """Set a control the way a user would: pick an option, press a button, focus an item."""
        _strings((control, value), f"drive({control!r}, {value!r}): control and value must be strings")
        self._request({"op": "drive", "control": control, "value": value})

    def leave(self, control, value):
        """Move focus off an item of a data-control-items container: a bubbling focusout, to nowhere."""
        _strings((control, value), f"leave({control!r}, {value!r}): control and value must be strings")
        self._request({"op": "leave", "control": control, "value": value})

    def chip(self, item, req, action="click"):
        """Click, or focus, the [data-req] element `req` inside the item `item` of a data-control-items container."""
        _strings((item, req, action), f"chip({item!r}, {req!r}, {action!r}): arguments must be strings")
        self._request({"op": "chip", "item": item, "req": req, "action": action})

    def key(self, name):
        """Press a key where the focus is: a bubbling keydown on the active element, else on the body."""
        _strings((name,), f"key({name!r}): the key name must be a string")
        self._request({"op": "key", "key": name})


@contextmanager
def open_page(path):
    """Load the page at path into jsdom, run its scripts, and yield a Page.

    The page's script runs with this process's rights: open only pages
    Gantry's own templates built.
    """
    page = Page()
    try:
        page._request({"op": "open", "path": str(Path(path).resolve())})
        yield page
    finally:
        page.stop()


# --- the shared driver --------------------------------------------------------------

_shared = None


def _shared_driver():
    """The driver run_function and node_version share; a fresh one after a failure."""
    global _shared
    if _shared is None or _shared.dead:
        _shared = _Driver()
    return _shared


@atexit.register
def _stop_shared():
    if _shared is not None:
        _shared.stop()


def node_version():
    """The version of the Node the harness runs, such as 24.20.0."""
    return _shared_driver()._request({"op": "version"})["node"]


def run_function(page_path, name, args=()):
    """Call the page function at a dotted name, such as gantryTest.pairs, in a fresh window.

    The name resolves one own property at a time from window, and a Map in
    the return value comes back as a list of [key, value] pairs.
    """
    if not isinstance(name, str) or isinstance(args, str):
        raise TypeError(f"run_function({name!r}, {args!r}): name must be a string and args a sequence, not a string")
    request = {"op": "call", "path": str(Path(page_path).resolve()), "name": name, "args": list(args)}
    return _shared_driver()._request(request)
