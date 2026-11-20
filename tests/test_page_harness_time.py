"""The page-script harness's clock, visibility, own HEAD, reload and served pages (#63).

Every test page is written to the test's own temporary folder. A page opened
without a clock behaves as #53 left it; these tests pin that too.
"""

import ast
import http.client
import http.server
import os
import socket
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helpers_docs import markdown_text
from helpers_gate import gate_run
from page_safety import HOSTILE
from page_script import harness
from page_script.gate import CI_SUFFIX
from page_script.harness import NODE_MISSING, HarnessError, PageScriptError, Planted, open_page
from test_page_checkers import IMG, KNOWN_BAD, RENDER_SCRIPT, embed, ext_page, hostile_page, write_page
from test_samples import ROOT

# 2026-11-05T07:45:00Z, and the same instant in milliseconds since the epoch.
T0 = datetime(2026, 11, 5, 7, 45, tzinfo=timezone.utc)
MS0 = 1793864700000


def page_text(body="", script=""):
    """A page that writes its location.href into #href, then runs script."""
    return ext_page('<meta charset="utf-8">\n<title>t</title>', "\n".join([
        '<p id="href"></p>',
        body,
        "<script>",
        'document.getElementById("href").textContent = location.href;',
        script,
        "</script>",
    ]))


def make_page(tmp_path, body="", script="", name="page.html"):
    """page_text written to tmp_path / name."""
    return write_page(tmp_path, page_text(body, script), name=name)


def buttons(*names):
    """Control-convention buttons, each pressed by drive(name, "now")."""
    return "".join(f'<button data-control="{n}" data-value="now">{n}</button>' for n in names)


def on_click(name, body):
    """Script running body when the button `name` is pressed."""
    return f'document.querySelector(\'[data-control="{name}"]\').addEventListener("click", function () {{ {body} }});'


def write(selector, expression):
    """Script writing expression's value as the text of the element at selector."""
    return f'document.querySelector("{selector}").textContent = String({expression});'


def text(page, selector):
    return page.query(selector)[0].text


def assert_unsent(monkeypatch, page, call, error, pattern):
    """call() raises error matching pattern and sends the driver nothing."""
    sent = []
    monkeypatch.setattr(page, "_request", sent.append)
    with pytest.raises(error, match=pattern):
        call()
    assert sent == []


# --- without a clock --------------------------------------------------------------


NO_CLOCK = [
    ("setTimeout", "setTimeout(function () {}, 0);", "runs no timer: setTimeout"),
    ("setInterval", "setInterval(function () {}, 0);", "runs no timer: setInterval"),
    ("requestAnimationFrame", "requestAnimationFrame(function () {});", "runs no timer: requestAnimationFrame"),
    ("own_head", 'fetch(location.href, {method: "HEAD"});', "loads no subresource: fetch"),
    ("reload", "location.reload();", "jsdom does not implement"),
]


@pytest.mark.parametrize("script,pattern", [c[1:] for c in NO_CLOCK], ids=[c[0] for c in NO_CLOCK])
def test_no_clock_as_today(node, tmp_path, script, pattern):
    with pytest.raises(PageScriptError, match=pattern):
        with open_page(make_page(tmp_path, script=script)):
            pass


CLOCK_METHODS = [
    ("advance", lambda page: page.advance(1)),
    ("set_clock", lambda page: page.set_clock(T0)),
    ("set_status", lambda page: page.set_status(200)),
    ("requests", lambda page: page.requests()),
    ("reloads", lambda page: page.reloads()),
]


@pytest.mark.parametrize("name,use", CLOCK_METHODS, ids=[c[0] for c in CLOCK_METHODS])
def test_controls_need_clock(node, tmp_path, name, use):
    with open_page(make_page(tmp_path, body='<p id="v">v1</p>')) as page:
        with pytest.raises(HarnessError, match=name + ": the page was opened without a clock"):
            use(page)
        assert text(page, "#v") == "v1"


@pytest.mark.parametrize("instant", [datetime(2026, 11, 5, 7, 45), "2026-11-05T07:45:00+00:00", MS0],
                         ids=["naive", "str", "int"])
def test_instant_must_be_aware(node, tmp_path, instant):
    with pytest.raises(TypeError, match="open_page: the instant must be a timezone-aware datetime"):
        with open_page(make_page(tmp_path), clock=instant):
            pass


def test_driver_runs_in_utc(node, tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    body = '<p id="s"></p><p id="off"></p>'
    script = write("#s", "new Date(0).toString()") + write("#off", "new Date(0).getTimezoneOffset()")
    with open_page(make_page(tmp_path, body, script)) as page:
        assert "GMT+0000" in text(page, "#s")
        assert text(page, "#off") == "0"


# --- the clock ----------------------------------------------------------------------


def test_clock_sets_date(node, tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    reads = {
        "now": "Date.now()",
        "s": "new Date().toString()",
        "d": "Date()",
        "inst": "new Date() instanceof Date",
        "ctor": "new Date().constructor === Date",
        "rebuilt": "new (new Date().constructor)().getTime()",
        "zero": "new Date(0).getTime()",
        "perf": "performance.now()",
    }
    body = "".join(f'<p id="{k}"></p>' for k in reads)
    script = "".join(write("#" + k, v) for k, v in reads.items())
    with open_page(make_page(tmp_path, body, script), clock=T0.astimezone(timezone(timedelta(hours=9)))) as page:
        assert text(page, "#now") == str(MS0)
        assert "07:45:00 GMT+0000" in text(page, "#s")
        assert "07:45:00 GMT+0000" in text(page, "#d")
        assert text(page, "#inst") == "true"
        assert text(page, "#ctor") == "true"
        assert text(page, "#rebuilt") == str(MS0)
        assert text(page, "#zero") == "0"
        assert text(page, "#perf") == "0"


ORDER = 'function order(c) { document.getElementById("order").textContent += c; }'


def test_advance_runs_due_timers(node, tmp_path):
    body = '<p id="count">0</p><p id="at"></p><p id="order"></p>' + buttons("stop")
    script = "\n".join([
        ORDER,
        "var count = 0;",
        "var iv = setInterval(function () {",
        '  count++; order("i");',
        "  " + write("#count", "count"),
        "  " + write("#at", "new Date().toISOString()"),
        "}, 60000);",
        'setTimeout(function () { order("t"); setTimeout(function () { order("n"); }, 10000); }, 30000);',
        'setTimeout(function () { order("u"); }, 30000);',
        on_click("stop", "clearInterval(iv);"),
    ])
    with open_page(make_page(tmp_path, body, script), clock=T0) as page:
        page.advance(59)
        assert text(page, "#count") == "0"
        assert text(page, "#order") == "tun"
        page.advance(1)
        assert text(page, "#count") == "1"
        assert text(page, "#at") == "2026-11-05T07:46:00.000Z"
        assert text(page, "#order") == "tuni"
        page.advance(120)
        assert text(page, "#count") == "3"
        assert text(page, "#at").endswith("07:48:00.000Z")
        page.drive("stop", "now")
        page.advance(600)
        assert text(page, "#count") == "3"


READ = on_click("read", write("#now", "Date.now()") + write("#perf", "performance.now()"))
READ_BODY = '<p id="now"></p><p id="perf"></p>' + buttons("read")
COUNTER = "var count = 0;\nsetInterval(function () { count++; " + write("#count", "count") + " }, {period});"


def counter(period_ms):
    return COUNTER.replace("{period}", str(period_ms))


def test_set_clock_moves_only_date(node, tmp_path):
    body = '<p id="count">0</p>' + READ_BODY
    with open_page(make_page(tmp_path, body, counter(60000) + "\n" + READ), clock=T0) as page:
        page.advance(59)
        page.set_clock(T0 - timedelta(minutes=6))
        page.drive("read", "now")
        assert text(page, "#now") == str(MS0 - 360000)
        assert text(page, "#perf") == "59000"
        assert text(page, "#count") == "0"
        page.advance(1)
        assert text(page, "#count") == "1"
        page.drive("read", "now")
        assert text(page, "#now") == str(MS0 - 359000)
        assert text(page, "#perf") == "60000"
        with pytest.raises(TypeError, match="set_clock: the instant must be a timezone-aware"):
            page.set_clock(datetime(2026, 11, 5, 7, 45))


BAD_SPANS = [
    ("str", "1", TypeError, "seconds must be a number"),
    ("none", None, TypeError, "seconds must be a number"),
    ("bool", True, TypeError, "seconds must be a number"),
    ("negative", -1, ValueError, "seconds must be finite and not negative"),
    ("nan", float("nan"), ValueError, "seconds must be finite and not negative"),
    ("inf", float("inf"), ValueError, "seconds must be finite and not negative"),
]


@pytest.mark.parametrize("seconds,error,pattern", [c[1:] for c in BAD_SPANS], ids=[c[0] for c in BAD_SPANS])
def test_advance_rejects_bad_spans(node, tmp_path, monkeypatch, seconds, error, pattern):
    with open_page(make_page(tmp_path), clock=T0) as page:
        assert_unsent(monkeypatch, page, lambda: page.advance(seconds), error, pattern)


def test_timer_error_stops_advance(node, tmp_path):
    body = '<p id="count">0</p><p id="late"></p>' + READ_BODY
    script = "\n".join([
        "var count = 0;",
        "setInterval(function () {",
        "  count++; " + write("#count", "count"),
        '  if (count === 2) throw new Error("gantry-timer-boom");',
        "}, 10000);",
        'setTimeout(function () { document.getElementById("late").textContent = "late"; }, 25000);',
        READ,
    ])
    with open_page(make_page(tmp_path, body, script), clock=T0) as page:
        with pytest.raises(PageScriptError, match="page script error: Error: gantry-timer-boom"):
            page.advance(60)
        assert text(page, "#count") == "2"
        assert text(page, "#late") == ""
        page.drive("read", "now")
        assert text(page, "#now") == str(MS0 + 20000)
        page.advance(5)
        assert text(page, "#late") == "late"


def test_timer_limit(node, tmp_path):
    with open_page(make_page(tmp_path, '<p id="count">0</p>', counter(0)), clock=T0) as page:
        with pytest.raises(PageScriptError, match="timer limit: more than 10000 timer runs in one advance"):
            page.advance(0)
        assert text(page, "#count") == "10000"


STILL_REFUSED = [
    ("requestAnimationFrame", "requestAnimationFrame(function () {});", "runs no timer: requestAnimationFrame"),
    ("string_handler", 'setTimeout("x()", 0);', "runs no timer: setTimeout handler is not a function"),
]


@pytest.mark.parametrize("script,pattern", [c[1:] for c in STILL_REFUSED], ids=[c[0] for c in STILL_REFUSED])
def test_clock_page_still_refuses(node, tmp_path, script, pattern):
    with pytest.raises(PageScriptError, match=pattern):
        with open_page(make_page(tmp_path, script=script), clock=T0):
            pass


# --- visibility ---------------------------------------------------------------------


LOG = 'function log(x) { var el = document.getElementById("log"); el.textContent += (el.textContent ? "," : "") + x; }'


@pytest.mark.parametrize("clock", [None, T0], ids=["no_clock", "clock"])
def test_set_visible_fires_event(node, tmp_path, clock):
    body = '<p id="load"></p><p id="log"></p>'
    pair = 'document.hidden + ":" + document.visibilityState'
    script = "\n".join([
        LOG,
        write("#load", pair),
        'document.addEventListener("visibilitychange", function () { log(' + pair + "); });",
    ])
    with open_page(make_page(tmp_path, body, script), clock=clock) as page:
        assert text(page, "#load") == "false:visible"
        page.set_visible(False)
        page.set_visible(True)
        assert text(page, "#log") == "true:hidden,false:visible"
        page.set_visible(True)
        assert text(page, "#log") == "true:hidden,false:visible"


@pytest.mark.parametrize("visible", [1, "yes", None], ids=["int", "str", "none"])
def test_set_visible_rejects_non_bool(node, tmp_path, monkeypatch, visible):
    with open_page(make_page(tmp_path)) as page:
        pattern = r"set_visible\(.*\): visible must be True or False"
        assert_unsent(monkeypatch, page, lambda: page.set_visible(visible), TypeError, pattern)


def test_hidden_holds_timers(node, tmp_path):
    body = '<p id="count">0</p><p id="at"></p>'
    script = "var count = 0;\nsetInterval(function () { count++; " + write("#count", "count") + write("#at", "Date.now()") + " }, 60000);"
    with open_page(make_page(tmp_path, body, script), clock=T0) as page:
        page.set_visible(False)
        page.advance(120)
        assert text(page, "#count") == "0"
        page.set_visible(True)
        assert text(page, "#count") == "0"
        page.advance(0)
        assert text(page, "#count") == "1"
        assert text(page, "#at") == str(MS0 + 120000)
        page.advance(59)
        assert text(page, "#count") == "1"
        page.advance(1)
        assert text(page, "#count") == "2"


STALE_CHECK = "\n".join([
    "var loaded = Date.now();",
    'function check() { document.getElementById("state").textContent = Date.now() - loaded > 300000 ? "stale" : "fresh"; }',
    "check();",
    "setInterval(check, 60000);",
])


@pytest.mark.parametrize("listener,shown", [(False, "fresh"), (True, "stale")], ids=["interval_only", "listener"])
def test_visibility_negative_control(node, tmp_path, listener, shown):
    script = STALE_CHECK + ('\ndocument.addEventListener("visibilitychange", check);' if listener else "")
    with open_page(make_page(tmp_path, '<p id="state"></p>', script), clock=T0) as page:
        assert text(page, "#state") == "fresh"
        page.set_visible(False)
        page.advance(600)
        page.set_visible(True)
        assert text(page, "#state") == shown
        page.advance(0)
        assert text(page, "#state") == "stale"


# --- the own HEAD and the request log -----------------------------------------------


OUT = 'function out(x) { document.getElementById("out").textContent = String(x); }'
ANSWER = ('function (r) { out(r.status + ":" + r.ok + ":" + r.statusText + ":" + Object.keys(r).sort().join("+")); }, '
          'function (e) { out(e.name + ":" + (e instanceof TypeError)); }')
OWN_HEAD = 'fetch(location.href, {method: "HEAD", cache: "no-store"})'
# The own HEAD every 60 s, its answer written to #out.
HEAD_EACH_MINUTE = OUT + "\nsetInterval(function () { " + OWN_HEAD + ".then(" + ANSWER + "); }, 60000);"
OK_200 = "200:true::ok+status+statusText"


def head_probe(name, call):
    """Script for the button `name`: call, its answer or "caught" written to #out."""
    return on_click(name, 'out(""); try { ' + call + ".then(" + ANSWER + '); } catch (e) { out("caught"); }')


def test_own_head_answered(node, tmp_path):
    settings = ["none", 200, 301, 304, 404, 502, 503, None]
    expected = ["TypeError:true", OK_200]
    expected += [f"{code}:false::ok+status+statusText" for code in (301, 304, 404, 502, 503)]
    expected += ["TypeError:true"]
    with open_page(make_page(tmp_path, '<p id="out"></p>', HEAD_EACH_MINUTE), clock=T0) as page:
        own = text(page, "#href")
        got = []
        for setting in settings:
            if setting != "none":
                page.set_status(setting)
            page.advance(60)
            got.append(text(page, "#out"))
        assert got == expected
        assert page.requests() == [harness.Request("HEAD", own, "no-store", "fetch")] * 8


MATCHING = [
    ("frag", 'fetch(location.href + "#x", {method: "head"})'),
    ("rel", 'fetch("page.html", {method: "HEAD"})'),
    ("query", 'fetch(location.href + "?v=1", {method: "HEAD"})'),
    ("own", 'fetch(location.href, {method: "HEAD"})'),
]


def test_own_head_matching(node, tmp_path):
    script = "\n".join([OUT] + [
        head_probe(name, call)
        for name, call in MATCHING
    ])
    body = '<p id="out"></p>' + buttons(*[m[0] for m in MATCHING])
    with open_page(make_page(tmp_path, body, script), clock=T0) as page:
        own = text(page, "#href")
        page.set_status(200)
        for name in ("frag", "rel"):
            page.drive(name, "now")
            assert text(page, "#out") == OK_200, name
            assert page.requests()[-1] == harness.Request("HEAD", own, "default", "fetch"), name
        with pytest.raises(PageScriptError, match=r"loads no subresource: fetch HEAD .*page\.html\?v=1"):
            page.drive("query", "now")
        page.drive("own", "now")
        assert text(page, "#out") == OK_200


@pytest.mark.parametrize("code", [199, 600, "200", 200.0, True], ids=["199", "600", "str", "float", "bool"])
def test_set_status_rejects(node, tmp_path, monkeypatch, code):
    with open_page(make_page(tmp_path), clock=T0) as page:
        pattern = "the status must be None or an integer from 200 to 599"
        assert_unsent(monkeypatch, page, lambda: page.set_status(code), ValueError, pattern)


# (id, call, match, method, url check, cache, api); the url check takes the own URL.
REFUSED = [
    ("own_get", "fetch(location.href)", "loads no subresource: fetch GET ",
     "GET", lambda url, own: url == own, "default", "fetch"),
    ("other_head", 'fetch("other.html", {method: "HEAD"})', "loads no subresource: fetch HEAD .*other.html",
     "HEAD", lambda url, own: url.endswith("/other.html"), "default", "fetch"),
    ("xhr", "new XMLHttpRequest()", "loads no subresource: XMLHttpRequest",
     None, lambda url, own: url is None, None, "XMLHttpRequest"),
    ("websocket", 'new WebSocket("ws://127.0.0.1:9/socket")', "loads no subresource: WebSocket",
     None, lambda url, own: url == "ws://127.0.0.1:9/socket", None, "WebSocket"),
    ("eventsource", 'new EventSource("events")', "loads no subresource: EventSource",
     None, lambda url, own: url.endswith("/events"), None, "EventSource"),
]


@pytest.mark.parametrize("call,pattern,method,url_ok,cache,api", [c[1:] for c in REFUSED], ids=[c[0] for c in REFUSED])
def test_other_requests_refused(node, tmp_path, call, pattern, method, url_ok, cache, api):
    script = OUT + "\n" + on_click("go", "try { " + call + '; } catch (e) { out("caught"); }') + "\n" + on_click("noop", "")
    with open_page(make_page(tmp_path, '<p id="out"></p>' + buttons("go", "noop"), script), clock=T0) as page:
        own = text(page, "#href")
        with pytest.raises(PageScriptError, match=pattern):
            page.drive("go", "now")
        assert text(page, "#out") == "caught"
        last = page.requests()[-1]
        assert (last.method, last.cache, last.api) == (method, cache, api)
        assert url_ok(last.url, own), last.url
        page.drive("noop", "now")


# --- reload and navigation ----------------------------------------------------------


def reload_on(answer):
    """A 60 s interval sending the own HEAD and running answer with its response r."""
    return "setInterval(function () { " + OWN_HEAD + ".then(function (r) { " + answer + " }, function () {}); }, 60000);"


RELOAD_ONLY_2XX = [
    ("200", "location.reload();", 200, 1),
    ("404", "location.reload();", 404, 0),
    ("503", "location.reload();", 503, 0),
    ("none", "location.reload();", None, 0),
    ("go0", "history.go(0);", 200, 1),
]


@pytest.mark.parametrize("call,status,expected", [c[1:] for c in RELOAD_ONLY_2XX], ids=[c[0] for c in RELOAD_ONLY_2XX])
def test_reload_only_on_2xx(node, tmp_path, call, status, expected):
    with open_page(make_page(tmp_path, script=reload_on("if (r.ok) " + call)), clock=T0) as page:
        page.set_status(status)
        page.advance(60)
        assert page.reloads() == expected
        assert len(page.requests()) == 1
        if expected:
            # The same file again: its timer runs on, and the status is kept.
            page.advance(60)
            assert page.reloads() == 2
            assert len(page.requests()) == 2


def test_reload_negative_control(node, tmp_path):
    with open_page(make_page(tmp_path, script=reload_on("location.reload();")), clock=T0) as page:
        page.set_status(503)
        page.advance(60)
        assert page.reloads() == 1


ITEMS = '<ul data-control="list" data-control-items><li data-item="a"><span data-req="r1" tabindex="0">r1</span></li></ul>'
READS = {"v": None, "now": "Date.now()", "hidden": "document.hidden", "perf": "performance.now()"}


def version_reads(version):
    return "".join(write("#" + k, f'"{version}"' if v is None else v) for k, v in READS.items())


READS_BODY = "".join(f'<p id="{k}"></p>' for k in READS) + '<div id="planted"></div><p id="log"></p>'


def test_reload_loads_again(node, tmp_path):
    v1 = "\n".join([
        version_reads("v1"),
        'document.getElementById("planted").textContent = ' + embed(HOSTILE[0]) + ";",
        reload_on(""),
        on_click("reload", "location.reload(); location.reload();"),
    ])
    v2 = "\n".join([
        version_reads("v2"),
        'document.getElementById("planted").innerHTML = ' + embed(HOSTILE[0]) + ";",
        LOG,
        on_click("press", 'log("drive");'),
        'document.querySelector("[data-control-items]").addEventListener("focusout", function () { log("leave"); });',
        'document.querySelector("[data-req]").addEventListener("click", function () { log("chip"); });',
        'document.addEventListener("keydown", function () { log("key"); });',
    ])
    path = make_page(tmp_path, READS_BODY + buttons("reload"), v1)
    with open_page(path, clock=T0) as page:
        page.advance(60)
        assert page.planted_nodes([HOSTILE[0]]).created == []
        page.remember("v", "#v")
        page.set_visible(False)
        page.advance(30)
        make_page(tmp_path, READS_BODY + buttons("press") + ITEMS, v2)
        page.drive("reload", "now")
        assert page.reloads() == 2
        assert text(page, "#v") == "v2"
        assert text(page, "#now") == str(MS0 + 90000)
        assert text(page, "#hidden") == "true"
        assert text(page, "#perf") == "0"
        assert page.same("v") is False
        assert len(page.requests()) == 1
        assert page.planted_nodes([HOSTILE[0]]).created != []
        page.set_visible(True)
        page.advance(600)
        assert len(page.requests()) == 1
        page.drive("press", "now")
        page.leave("list", "a")
        page.chip("a", "r1")
        page.key("Enter")
        assert text(page, "#log") == "drive,leave,chip,key"


def test_reload_during_advance(node, tmp_path):
    path = make_page(tmp_path, script="setTimeout(function () { location.reload(); }, 60000);")
    with open_page(path, clock=T0) as page:
        make_page(tmp_path, '<p id="now"></p>', "setTimeout(function () { " + write("#now", "Date.now()") + " }, 10000);")
        page.advance(75)
        assert page.reloads() == 1
        assert text(page, "#now") == str(MS0 + 70000)


RELOAD_LIMIT = "reload limit: more than 100 reloads in one step"
EVERY_SECOND = "setInterval(function () { location.reload(); }, 1000);"


@pytest.mark.parametrize("leg", ["hundred", "over", "at_load"])
def test_reload_limit(node, tmp_path, leg):
    if leg == "at_load":
        with pytest.raises(PageScriptError, match=RELOAD_LIMIT):
            with open_page(make_page(tmp_path, script="location.reload();"), clock=T0):
                pass
        return
    with open_page(make_page(tmp_path, '<p id="v">v</p>', EVERY_SECOND), clock=T0) as page:
        if leg == "hundred":
            page.advance(100)
            assert page.reloads() == 100
            # The limit is per step: the next step may carry out 100 more.
            page.advance(100)
            assert page.reloads() == 200
        else:
            with pytest.raises(PageScriptError, match=RELOAD_LIMIT):
                page.advance(200)
            # The 101st call is counted, not carried out.
            assert page.reloads() == 101
        assert text(page, "#v") == "v"


def test_reload_dropped_after_error(node, tmp_path):
    script = on_click("boom", 'location.reload(); throw new Error("gantry-after-reload");') + on_click("noop", "")
    body = buttons("boom", "noop")
    path = make_page(tmp_path, '<p id="v">v1</p>' + body, script)
    with open_page(path, clock=T0) as page:
        with pytest.raises(PageScriptError, match="page script error: Error: gantry-after-reload"):
            page.drive("boom", "now")
        assert page.reloads() == 1
        make_page(tmp_path, '<p id="v">v2</p>' + body, script)
        assert text(page, "#v") == "v1"
        page.drive("noop", "now")
        assert page.reloads() == 1
        assert text(page, "#v") == "v1"


# (id, a step run first, the navigation)
NAVIGATIONS = [
    ("href_other", "", 'location.href = "other.html";'),
    ("href_self", "", "location.href = location.href;"),
    ("assign_self", "", "location.assign(location.href);"),
    ("replace_self", "", "location.replace(location.href);"),
    ("drop_fragment", 'location.hash = "#a";', "location.href = location.pathname;"),
    ("same_fragment", 'location.hash = "#a";', "location.href = location.href;"),
]


@pytest.mark.parametrize("first,navigation", [c[1:] for c in NAVIGATIONS], ids=[c[0] for c in NAVIGATIONS])
def test_navigation_refused(node, tmp_path, first, navigation):
    script = on_click("first", first) + on_click("go", navigation)
    with open_page(make_page(tmp_path, buttons("first", "go"), script), clock=T0) as page:
        page.drive("first", "now")
        with pytest.raises(PageScriptError, match="follows no navigation"):
            page.drive("go", "now")
        assert page.reloads() == 0


def test_navigation_refused_at_load(node, tmp_path):
    with pytest.raises(PageScriptError, match="follows no navigation"):
        with open_page(make_page(tmp_path, script="location.replace(location.href);"), clock=T0):
            pass


def test_fragment_change_allowed(node, tmp_path):
    script = "\n".join([
        on_click("hash", 'location.hash = "#a";'),
        on_click("clear", 'location.hash = "";'),
        on_click("read", write("#at", "location.href")),
        on_click("head", 'fetch(location.href, {method: "HEAD"});'),
    ])
    with open_page(make_page(tmp_path, '<p id="at"></p>' + buttons("hash", "clear", "read", "head"), script),
                   clock=T0) as page:
        own = text(page, "#href")
        page.drive("hash", "now")
        page.drive("read", "now")
        assert text(page, "#at") == own + "#a"
        assert page.reloads() == 0
        page.set_status(200)
        page.drive("head", "now")
        assert page.requests()[-1] == harness.Request("HEAD", own, "default", "fetch")
        page.drive("clear", "now")
        page.drive("read", "now")
        assert text(page, "#at") == own + "#"
        assert page.reloads() == 0


def _listen(target, event):
    return lambda body: f'{target}.addEventListener("{event}", function () {{ {body} }});'


# op -> (markup, listener taking a body, the step)
TRIGGERS = {
    "drive": (buttons("go"), lambda body: on_click("go", body), lambda page: page.drive("go", "now")),
    "leave": (ITEMS, _listen('document.querySelector("[data-control-items]")', "focusout"),
              lambda page: page.leave("list", "a")),
    "chip": (ITEMS, _listen('document.querySelector("[data-req]")', "click"), lambda page: page.chip("a", "r1")),
    "key": ("", _listen("document", "keydown"), lambda page: page.key("Enter")),
    "set_visible": ("", _listen("document", "visibilitychange"), lambda page: page.set_visible(False)),
    "advance": ("", lambda body: "setTimeout(function () { " + body + " }, 1000);", lambda page: page.advance(1)),
}


@pytest.mark.parametrize("op", list(TRIGGERS))
def test_every_step_ends_in_finish(node, tmp_path, op):
    markup, listen, step = TRIGGERS[op]
    with open_page(make_page(tmp_path, '<p id="v">v1</p>' + markup, listen("location.reload();")), clock=T0) as page:
        make_page(tmp_path, '<p id="v">v2</p>')
        step(page)
        assert page.reloads() == 1
        assert text(page, "#v") == "v2"
    refusing = make_page(tmp_path, markup, listen('try { fetch("other.html"); } catch (e) {}'), name="refusing.html")
    with open_page(refusing, clock=T0) as page:
        with pytest.raises(PageScriptError, match="loads no subresource"):
            step(page)


def test_planted_reads_current_page(node, tmp_path):
    script = "setTimeout(function () {\n" + RENDER_SCRIPT + "\n}, 1000);"
    with open_page(write_page(tmp_path, hostile_page(values=[], script=script)), clock=T0) as page:
        assert page.planted_nodes(HOSTILE).unrendered == list(HOSTILE)
        page.advance(1)
        assert page.planted_nodes(HOSTILE) == Planted([], [], [])


def test_raw_sink_with_clock(node):
    with open_page(KNOWN_BAD / "raw_sink.html", clock=T0) as page:
        found = page.planted_nodes([IMG])
    assert {"node": "element", "tag": "img", "value": IMG} in found.created
    assert {"node": "attribute", "tag": "img", "attr": "onerror", "value": IMG} in found.created


# --- served pages -------------------------------------------------------------------


class Site:
    """A loopback http.server over a folder, logging each (command, path) it is sent."""

    def __init__(self, folder):
        self.folder = folder
        self.log = []
        # path -> function of the GET count so far (1 for the first) giving the body.
        self.dynamic = {}
        self.port = None

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"


def _handler(site):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(site.folder), **kwargs)

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            site.log.append(("GET", self.path))
            respond = site.dynamic.get(self.path)
            if respond is None:
                return super().do_GET()
            body = respond(site.log.count(("GET", self.path))).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None

        def do_HEAD(self):
            site.log.append(("HEAD", self.path))
            return super().do_HEAD()

    return Handler


@pytest.fixture
def served(tmp_path):
    """A Site on 127.0.0.1, an OS-chosen port, serving tmp_path / "site"."""
    site = Site(tmp_path / "site")
    site.folder.mkdir()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(site))
    site.port = server.server_address[1]
    # shutdown() waits out a poll interval: poll often.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def closed_port():
    """A loopback port nothing listens on: bound by the OS, then closed."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_open_url_loads(node, served):
    for name, data in (("s.js", b'document.title = "loaded";'), ("s.css", b"body { color: red }"), ("i.png", b"png")):
        (served.folder / name).write_bytes(data)
    body = '<script src="s.js"></script><link rel="stylesheet" href="s.css"><img src="i.png"><p id="out"></p>'
    make_page(served.folder, body, HEAD_EACH_MINUTE)
    url = served.url("page.html")
    with harness.open_url(url, clock=T0) as page:
        assert text(page, "#href") == url
        assert text(page, "title") == "t"
        page.set_status(200)
        page.advance(60)
        assert text(page, "#out") == OK_200
        assert page.requests() == [harness.Request("HEAD", url, "no-store", "fetch")]
    assert served.log == [("GET", "/page.html")]
    # The log could have shown a HEAD: the test's own reaches it.
    connection = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
    try:
        connection.request("HEAD", "/page.html")
        connection.getresponse().read()
    finally:
        connection.close()
    assert served.log == [("GET", "/page.html"), ("HEAD", "/page.html")]


def test_own_url_fixed_at_open(node, served):
    # An http page may push another path; a file: page may not (jsdom, History-impl.js).
    script = "\n".join([OUT] + [
        head_probe(name, call)
        for name, call in MATCHING if name in ("own", "rel")
    ] + [on_click("push", 'history.pushState(null, "", "pushed.html");')])
    make_page(served.folder, '<p id="out"></p>' + buttons("own", "rel", "push"), script)
    with harness.open_url(served.url("page.html"), clock=T0) as page:
        page.set_status(200)
        page.drive("push", "now")
        with pytest.raises(PageScriptError, match="loads no subresource: fetch HEAD .*pushed.html"):
            page.drive("own", "now")
        page.drive("rel", "now")
        assert text(page, "#out") == OK_200
        assert page.requests()[-1] == harness.Request("HEAD", served.url("page.html"), "default", "fetch")


def test_served_reload(node, served):
    make_page(served.folder, '<p id="v">v1</p>', reload_on("if (r.ok) location.reload();"))
    with harness.open_url(served.url("page.html"), clock=T0) as page:
        make_page(served.folder, '<p id="v">v2</p>')
        page.set_status(200)
        page.advance(60)
        assert page.reloads() == 1
        assert text(page, "#v") == "v2"
    assert served.log == [("GET", "/page.html")] * 2


def test_served_reload_at_load(node, served):
    served.dynamic["/page.html"] = lambda n: page_text(f'<p id="v">v{n}</p>', "location.reload();" if n <= 3 else "")
    with harness.open_url(served.url("page.html"), clock=T0) as page:
        assert page.reloads() == 3
        assert text(page, "#v") == "v4"
    assert served.log == [("GET", "/page.html")] * 4


# Each a URL open_url refuses; port is the served port.
REFUSED_URLS = [
    ("non_loopback", lambda tmp_path, port: f"http://10.0.0.1:{port}/page.html"),
    ("localhost", lambda tmp_path, port: f"http://localhost:{port}/page.html"),
    ("https", lambda tmp_path, port: f"https://127.0.0.1:{port}/page.html"),
    ("file", lambda tmp_path, port: (tmp_path / "page.html").as_uri()),
    ("subdomain_label", lambda tmp_path, port: f"http://127.0.0.1.example.com:{port}/page.html"),
    ("userinfo_before", lambda tmp_path, port: f"http://user@127.0.0.1:{port}/page.html"),
    ("userinfo_is_address", lambda tmp_path, port: f"http://127.0.0.1@example.com:{port}/page.html"),
    ("upper_scheme", lambda tmp_path, port: f"HTTP://127.0.0.1:{port}/page.html"),
    ("decimal", lambda tmp_path, port: f"http://2130706433:{port}/page.html"),
    ("short", lambda tmp_path, port: f"http://127.1:{port}/page.html"),
    ("port_over_65535", lambda tmp_path, port: "http://127.0.0.1:99999/page.html"),
]


def _no_page():
    raise AssertionError("a Page was built for a refused URL")


@pytest.mark.parametrize("build", [c[1] for c in REFUSED_URLS], ids=[c[0] for c in REFUSED_URLS])
def test_open_url_refuses(tmp_path, monkeypatch, served, build):
    monkeypatch.setattr(harness, "Page", _no_page)
    with pytest.raises(HarnessError, match=r"open_url\(.*\): only an http:// URL on 127\.0\.0\.1 or \[::1\] is opened"):
        with harness.open_url(build(tmp_path, served.port), clock=T0):
            pass
    assert served.log == []


def test_open_url_refuses_non_string(monkeypatch):
    monkeypatch.setattr(harness, "Page", _no_page)
    with pytest.raises(TypeError, match=r"open_url\(b'.*'\): the URL must be a string"):
        with harness.open_url(b"http://127.0.0.1/page.html"):
            pass


def test_open_url_admits_ipv6(node):
    # Refused by the connection, not by the rule: no IPv6 listener is needed.
    with pytest.raises(HarnessError, match=r"cannot fetch http://\[::1\]:[0-9]+/page\.html: "):
        with harness.open_url(f"http://[::1]:{closed_port()}/page.html", clock=T0):
            pass


@pytest.mark.parametrize("leg", ["missing", "redirect", "closed_port", "reload_missing"])
def test_open_url_bad_answer(node, served, leg):
    if leg == "missing":
        with pytest.raises(HarnessError, match=r"cannot fetch .*/missing\.html: status 404"):
            with harness.open_url(served.url("missing.html"), clock=T0):
                pass
        return
    if leg == "redirect":
        # http.server answers a folder without its slash with a 301 and a Location.
        (served.folder / "sub").mkdir()
        with pytest.raises(HarnessError, match=r"cannot fetch .*/sub: status 301"):
            with harness.open_url(served.url("sub"), clock=T0):
                pass
        assert served.log == [("GET", "/sub")]
        return
    if leg == "closed_port":
        with pytest.raises(HarnessError, match="cannot fetch http://127.0.0.1:[0-9]+/page.html: "):
            with harness.open_url(f"http://127.0.0.1:{closed_port()}/page.html", clock=T0):
                pass
        return
    path = make_page(served.folder, script="setTimeout(function () { location.reload(); }, 1000);")
    with harness.open_url(served.url("page.html"), clock=T0) as page:
        path.unlink()
        with pytest.raises(HarnessError, match="cannot fetch .*/page.html: status 404"):
            page.advance(1)
        with pytest.raises(HarnessError, match="no page is open"):
            page.query("p")


def test_driver_rechecks_loopback(node, served):
    # Past harness.py's rule, straight to the driver: it refuses the host itself.
    make_page(served.folder)
    page = harness.Page()
    try:
        with pytest.raises(HarnessError, match="not a loopback http URL"):
            page._request({"op": "open", "url": f"http://localhost:{served.port}/page.html", "clock": MS0})
    finally:
        page.stop()
    assert served.log == []


@pytest.mark.parametrize("leg", ["env", "agent"])
def test_open_url_no_proxy(node, served, monkeypatch, leg):
    make_page(served.folder, '<p id="v">v1</p>')
    proxy = f"http://127.0.0.1:{closed_port()}"
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, proxy)
    monkeypatch.setenv("NODE_USE_ENV_PROXY", "1")
    if leg == "env":
        envs = []
        real_popen = subprocess.Popen

        def recording(*args, **kwargs):
            envs.append(kwargs.get("env"))
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(harness.subprocess, "Popen", recording)
    else:
        # The env defence off: the driver's own Agent must still go direct.
        monkeypatch.setattr(harness, "DROPPED_ENV", ("NODE_OPTIONS", "NODE_PATH"))
    with harness.open_url(served.url("page.html"), clock=T0) as page:
        assert text(page, "#v") == "v1"
    if leg == "env":
        assert envs and all("NODE_USE_ENV_PROXY" not in env for env in envs)
    assert served.log == [("GET", "/page.html")]


def test_open_url_without_clock(node, served):
    make_page(served.folder, script="setTimeout(function () {}, 0);")
    with pytest.raises(PageScriptError, match="runs no timer: setTimeout"):
        with harness.open_url(served.url("page.html")):
            pass


# --- the doc ------------------------------------------------------------------------


DOC_SPANS = [
    "clock=", "advance", "set_clock", "TZ=UTC", "set_visible", "visibilitychange", "set_status", "requests",
    "api", "reloads", "location.reload()", "location.assign", "location.replace", "location.href",
    "location.hash", "open_url", "127.0.0.1", "[::1]", "localhost", "HEAD",
]


def test_doc_covers_time_controls():
    doc = markdown_text(ROOT / "docs" / "page-safety.md")
    assert [s for s in DOC_SPANS if f"`{s}`" not in doc] == []
    limits = doc.split("\n## Limits\n", 1)[1].split("\n## ", 1)[0]
    assert [s for s in ("Intl", "Event.timeStamp", "performance.timeOrigin") if f"`{s}`" not in limits] == []
    assert "The harness runs no timer:" not in limits


# 2020-01-01T00:00:00Z in ms: a real clock read is past it, a clock in 2001 is not.
MS_2020 = 1577836800000
REAL_CLOCK_READS = [
    ("intl", 'new Intl.DateTimeFormat("en", {year: "numeric", timeZone: "UTC"}).format()', lambda v: v != "2001"),
    ("event_timestamp", 'new Event("x").timeStamp', lambda v: float(v) > MS_2020),
    ("time_origin", "performance.timeOrigin", lambda v: float(v) > MS_2020),
    ("last_modified", "Date.parse(document.lastModified)", lambda v: float(v) > MS_2020),
]


@pytest.mark.parametrize("expression,real", [c[1:] for c in REAL_CLOCK_READS], ids=[c[0] for c in REAL_CLOCK_READS])
def test_real_clock_reads_hold(node, tmp_path, expression, real):
    with open_page(make_page(tmp_path, '<p id="out"></p>', write("#out", expression)),
                   clock=datetime(2001, 1, 1, tzinfo=timezone.utc)) as page:
        value = text(page, "#out")
    assert real(value), value


def test_explicit_locale_text_is_stable(node, tmp_path, monkeypatch):
    path = make_page(tmp_path, '<p id="gb"></p><p id="any"></p>',
                     write("#gb", 'new Date().toLocaleTimeString("en-GB")') + write("#any", "new Date().toLocaleTimeString()"))
    seen = []
    for locale in ("en_US.UTF-8", "de_DE.UTF-8"):
        monkeypatch.setenv("LC_ALL", locale)
        with open_page(path, clock=T0) as page:
            seen.append((text(page, "#gb"), text(page, "#any")))
    assert [gb for gb, _ in seen] == ["07:45:00", "07:45:00"]
    if os.name == "posix":
        # Text formatted with no locale follows the machine's (the doc's Limits); unmeasured on Windows.
        assert seen[0][1] != seen[1][1], seen


# --- the gate: these tests run, never skip ------------------------------------------

# The test functions that take no node; every other one does.
STATIC = {"test_open_url_refuses", "test_open_url_refuses_non_string", "test_doc_covers_time_controls",
          "test_time_tests_never_skip", "test_time_gate_end_to_end"}
# Floors, not exact counts: raise them when adding tests to this module.
GATED_FLOOR = 42
NODE_CASES = 93
STATIC_CASES = 14


def test_time_tests_never_skip():
    tree = ast.parse(Path(__file__).read_bytes().decode("utf-8"))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert used & {"skip", "skipif", "xfail", "importorskip"} == set()
    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    gated = [t.name for t in tests if "node" in [a.arg for a in t.args.args]]
    assert {t.name for t in tests} - set(gated) == STATIC
    assert len(gated) >= GATED_FLOOR


@pytest.mark.parametrize("ci", [None, "true"], ids=["unset", "true"])
def test_time_gate_end_to_end(tmp_path, ci):
    code, cases = gate_run(tmp_path, ci, module="tests/test_page_harness_time.py")
    passed = [kind for kind, _ in cases].count("passed")
    others = [(kind, message) for kind, message in cases if kind != "passed"]
    if ci:
        # A setup-time pytest.fail is an <error> wrapping the reason.
        assert all(kind in ("failure", "error") and NODE_MISSING + CI_SUFFIX in message for kind, message in others), others
        assert code == 1
    else:
        assert all(kind == "skipped" and message == NODE_MISSING for kind, message in others), others
        assert code == 0
    assert len(others) >= NODE_CASES
    assert passed >= STATIC_CASES
