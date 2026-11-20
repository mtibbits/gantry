"""The page-script harness for Gantry's generated pages (#53).

A plain module, not a test module, imported as page_script.harness. It imports
only the standard library and page_safety, and drives a Node child running
driver.js, which loads a page into jsdom: from a file, or from a loopback URL
(open_url), with or without a controlled clock (#63). See docs/page-safety.md
for the protocol, the gate and the limits.
"""

import atexit
import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

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
# The driver's environment never takes these from ours; NODE_USE_ENV_PROXY
# would route Node's global agent through a proxy (#63).
DROPPED_ENV = ("NODE_OPTIONS", "NODE_PATH", "NODE_USE_ENV_PROXY")
# The URLs open_url opens, matched on the string as written: http on the
# loopback address, never localhost (#63).
LOOPBACK_URL = re.compile(r"http://(?:127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?(?:/[!-~]*)?")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class PageScriptError(Exception):
    """The page failed: a script error, a jsdom gap, or a missing control or value."""


class HarnessError(Exception):
    """The harness itself failed: Node or jsdom not ready, the driver gone or silent."""


class Element(NamedTuple):
    tag: str
    attrs: dict
    classes: list
    text: str


class Request(NamedTuple):
    """A request a clock page made; None where the call stated nothing."""

    method: str | None
    url: str | None
    cache: str | None
    api: str


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
        # One time zone on every machine; the locale is left alone (#63).
        env["TZ"] = "UTC"
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


def _instant_ms(name, value):
    """A timezone-aware datetime as whole milliseconds since the epoch."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{name}: the instant must be a timezone-aware datetime, not {value!r}")
    return (value - EPOCH) // timedelta(milliseconds=1)


class Page(_Driver):
    """A page loaded into jsdom by open_page."""

    # Whether the page was opened with a controlled clock.
    _clock = False

    def _need_clock(self, name):
        if not self._clock:
            raise HarnessError(f"{name}: the page was opened without a clock")

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

    def remember(self, name, selector):
        """Keep, under `name`, the elements the selector matches now; return how many."""
        _strings((name, selector), f"remember({name!r}, {selector!r}): name and selector must be strings")
        return self._request({"op": "remember", "name": name, "selector": selector})

    def same(self, name):
        """Whether the selector remembered as `name` matches the same nodes, as many, in the same order."""
        _strings((name,), f"same({name!r}): the name must be a string")
        return self._request({"op": "same", "name": name})

    def set_visible(self, visible):
        """Show or hide the page; a change fires visibilitychange on the document."""
        if not isinstance(visible, bool):
            raise TypeError(f"set_visible({visible!r}): visible must be True or False")
        self._request({"op": "set_visible", "visible": visible})

    def advance(self, seconds):
        """Move the clock forward and run every timer that falls due in the span."""
        self._need_clock("advance")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise TypeError(f"advance({seconds!r}): seconds must be a number")
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"advance({seconds!r}): seconds must be finite and not negative")
        # Whole milliseconds.
        self._request({"op": "advance", "ms": round(seconds * 1000)})

    def set_clock(self, instant):
        """Set the clock Date reads; no timer runs and performance.now() stays."""
        self._need_clock("set_clock")
        self._request({"op": "set_clock", "ms": _instant_ms("set_clock", instant)})

    def set_status(self, code):
        """The status the page's own HEAD is answered with; None makes it reject."""
        self._need_clock("set_status")
        if code is not None and (isinstance(code, bool) or not isinstance(code, int) or not 200 <= code <= 599):
            raise ValueError(f"set_status({code!r}): the status must be None or an integer from 200 to 599")
        self._request({"op": "set_status", "status": code})

    def requests(self):
        """Every request the page made, refused ones included, in order."""
        self._need_clock("requests")
        return [Request(r["method"], r["url"], r["cache"], r["api"]) for r in self._request({"op": "requests"})]

    def reloads(self):
        """How many times the page called location.reload()."""
        self._need_clock("reloads")
        return self._request({"op": "reloads"})


@contextmanager
def open_page(path, clock=None):
    """Load the page at path into jsdom, run its scripts, and yield a Page.

    With clock, a timezone-aware datetime, the page runs on a controlled
    clock set to that instant. The page's script runs with this process's
    rights: open only pages Gantry's own templates built.
    """
    with _open("open_page", {"path": str(Path(path).resolve())}, clock) as page:
        yield page


@contextmanager
def _open(name, source, clock):
    """Open source, the open request's path or url field, and yield the Page."""
    ms = None if clock is None else _instant_ms(name, clock)
    page = Page()
    page._clock = ms is not None
    try:
        page._request({"op": "open", **source, "clock": ms})
        yield page
    finally:
        page.stop()


def _loopback(url):
    """Whether open_url opens url: LOOPBACK_URL, and a port of at most 65535."""
    if LOOPBACK_URL.fullmatch(url) is None:
        return False
    try:
        urlsplit(url).port
    except ValueError:
        return False
    return True


@contextmanager
def open_url(url, clock=None):
    """Fetch the page at a loopback http:// URL with GET, load it into jsdom, and yield a Page.

    Only http:// on 127.0.0.1 or [::1] (never localhost) with a port of at
    most 65535 is opened, through no proxy; a non-2xx answer or no answer
    raises HarnessError, on open and on reload. The URL is the page's own
    URL, and clock is as for open_page. open_url runs whatever answers on
    the loopback port, with this process's rights, so it is only for pages
    a test serves itself.
    """
    if not isinstance(url, str):
        raise TypeError(f"open_url({url!r}): the URL must be a string")
    if not _loopback(url):
        raise HarnessError(f"open_url({url!r}): only an http:// URL on 127.0.0.1 or [::1] is opened")
    with _open("open_url", {"url": url}, clock) as page:
        yield page


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
