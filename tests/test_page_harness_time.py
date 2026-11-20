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
