"""The page-script harness's clock, visibility, own HEAD, reload and served pages (#63).

Every test page is written to the test's own temporary folder. A page opened
without a clock behaves as #53 left it; these tests pin that too.
"""

from datetime import datetime, timedelta, timezone

import pytest

from page_script import harness
from page_script.harness import HarnessError, PageScriptError, open_page
from test_page_checkers import write_page

# 2026-11-05T07:45:00Z, and the same instant in milliseconds since the epoch.
T0 = datetime(2026, 11, 5, 7, 45, tzinfo=timezone.utc)
MS0 = 1793864700000


def make_page(tmp_path, body="", script="", name="page.html"):
    """A page that writes its location.href into #href, then runs script."""
    text = "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>t</title>",
        "</head>",
        "<body>",
        '<p id="href"></p>',
        body,
        "<script>",
        'document.getElementById("href").textContent = location.href;',
        script,
        "</script>",
        "</body>",
        "</html>",
    ]) + "\n"
    return write_page(tmp_path, text, name=name)


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
        sent = []
        monkeypatch.setattr(page, "_request", sent.append)
        with pytest.raises(error, match=pattern):
            page.advance(seconds)
    assert sent == []


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
        sent = []
        monkeypatch.setattr(page, "_request", sent.append)
        with pytest.raises(TypeError, match=r"set_visible\(.*\): visible must be True or False"):
            page.set_visible(visible)
    assert sent == []


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


def test_own_head_answered(node, tmp_path):
    script = OUT + "\nsetInterval(function () { " + OWN_HEAD + ".then(" + ANSWER + "); }, 60000);"
    settings = ["none", 200, 301, 304, 404, 502, 503, None]
    expected = ["TypeError:true", "200:true::ok+status+statusText"]
    expected += [f"{code}:false::ok+status+statusText" for code in (301, 304, 404, 502, 503)]
    expected += ["TypeError:true"]
    with open_page(make_page(tmp_path, '<p id="out"></p>', script), clock=T0) as page:
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
        on_click(name, 'out(""); try { ' + call + ".then(" + ANSWER + '); } catch (e) { out("caught"); }')
        for name, call in MATCHING
    ])
    body = '<p id="out"></p>' + buttons(*[m[0] for m in MATCHING])
    with open_page(make_page(tmp_path, body, script), clock=T0) as page:
        own = text(page, "#href")
        page.set_status(200)
        for name in ("frag", "rel"):
            page.drive(name, "now")
            assert text(page, "#out") == "200:true::ok+status+statusText", name
            assert page.requests()[-1] == harness.Request("HEAD", own, "default", "fetch"), name
        with pytest.raises(PageScriptError, match=r"loads no subresource: fetch HEAD .*page\.html\?v=1"):
            page.drive("query", "now")
        page.drive("own", "now")
        assert text(page, "#out") == "200:true::ok+status+statusText"


@pytest.mark.parametrize("code", [199, 600, "200", 200.0, True], ids=["199", "600", "str", "float", "bool"])
def test_set_status_rejects(node, tmp_path, monkeypatch, code):
    with open_page(make_page(tmp_path), clock=T0) as page:
        sent = []
        monkeypatch.setattr(page, "_request", sent.append)
        with pytest.raises(ValueError, match="the status must be None or an integer from 200 to 599"):
            page.set_status(code)
    assert sent == []


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
