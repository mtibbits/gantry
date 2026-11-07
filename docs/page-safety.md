# Page safety checkers

Gantry's pages put project text, written by people, into HTML. A title that
holds markup must show as text, never run as script, and a page must never
load anything from outside itself. Two static checkers in
`tests/page_safety.py`, and the page-script harness in
`tests/page_script/harness.py`, hold every view's tests to that:

- `page_findings` and `assert_page_safe` check a page against the **page
  rules** and the embedded-data contract;
- `assert_no_external_resources` checks that no listed URL attribute, style
  or meta can load a resource, and that the page's own CSP meta comes first
  in the head;
- the [page-script harness](#page-script-harness) runs the page's own script
  in jsdom and checks what it built, which no static read can see.

The two static checkers read the page text with one standard-library
`html.parser` pass. All three are test helpers, not part of the installed
package: a view's tests import the checkers as `page_safety`, the way they
import `helpers_config`, and the harness as `page_script.harness`.

## The page rules

A page breaks the rules when it holds:

1. a tag not in `allowed_tags`;
2. an attribute not in `allowed_attrs` (every one is reported, including one
   on a tag that is itself not allowed);
3. a comment, in any of its forms;
4. a `script` whose `id` is not in `own_script_ids` (a script with no id
   breaks this rule too).

`HOSTILE` is the list of strings a view's tests plant in its records. Each
one targets a context a value can escape from:

| Entry | Why it is there |
|---|---|
| a script element | the plainest injection in element context |
| a script end tag, then a script | breaks out of a script block that embeds the value raw |
| an image with an error handler | an element with an event handler, no script tag needed |
| a quote, a closing bracket, then an svg with a load handler | breaks out of a double-quoted attribute |
| a single quote, then a mouse-over handler | breaks out of a single-quoted attribute |
| a comment opener | swallows the rest of a page that embeds it raw |
| the line and paragraph separators | legal in JSON, but they end a line in older script parsers |
| `__proto__` | an object key that a careless merge turns into a prototype change |
| `{{DATA}}` | a template placeholder that must stay text, never be filled twice |
| an already-escaped ampersand | catches double escaping and double unescaping |
| title, style and textarea end tags, then a script | breaks out of a raw-text element, such as the page title, that embeds the value raw |

## `page_rules.json`

The rules live in `tests/page_script/page_rules.json`, a JSON object with
exactly three keys, each a sorted list of lower-case names:

- `allowed_tags`: element names;
- `allowed_attrs`: attribute names;
- `own_script_ids`: the ids of the page's own script blocks, the embedded
  data among them.

`check_rules` is the one check behind the file, the `rules=` argument and
the [page-script harness](#page-script-harness). It returns each list as a
frozenset, and it raises `ValueError` naming the defect when the value is
not an object, the keys are
not exactly those three, a value is not a list, an item is not a non-empty
lower-case string, an item is listed twice, or an `allowed_attrs` item is an
event handler (it starts with `on`). `RULES` is the parsed file, and it is
checked when the module is imported, so a bad file stops every test that
imports `page_safety`.

Both page checkers take `rules=`. When it is left out they check `RULES` at
call time, so a test can swap the module's `RULES` and see the change.

A view that needs a new tag, attribute or script id adds it to the file in
its own change, with the tests that show it rendering safely. An `on*`
attribute is never added: `check_rules` refuses one. The file lives under
`tests/page_script/` because it describes test pages, not the package.

## `page_findings` and `assert_page_safe`

`page_findings(page, rules=None)` returns a list of `Finding` tuples in
document order. Names are lower-case.

| `rule` | `tag` | `attr` | `line` |
|---|---|---|---|
| `tag` | the tag | `None` | the tag's line |
| `attribute` | the tag | the attribute | the tag's line |
| `comment` | `None` | `None` | where the comment starts |
| `script` | `script` | its id, or `None` when it has none | the tag's line |

A comment is anything `html.parser` reports as a comment, a processing
instruction or a declaration other than the doctype: the ordinary form, a
question-mark form, a CDATA section and a conditional comment each give one
finding, on every Python version CI runs.

Inside an `svg` or `math`, no element's text is read raw: the children of
`title`, `style` and `script` are read as markup, the way a browser reads
them there, so a raw value in an SVG tooltip is found, and so is one in an
own-id script nested in an `svg`.

`assert_page_safe(page, planted, rules=None)` lists every finding, then
checks the embedded data:

- the page holds exactly one `<script type="application/json"
  id="gantry-data">` (both attributes exact);
- no other `script` carries `id="gantry-data"`: the id is an own script id,
  so the page rules alone would let one run;
- its text is JSON (`NaN` and `Infinity` are refused);
- every planted value is in the data, as a string value or an object key, at
  any depth.

It raises one `AssertionError` listing every problem, each with its line
where it has one (a missing, doubled or non-JSON data block has none), or
returns the parsed data. `planted` is a collection of strings; a bare string
is refused with `TypeError`.

## `assert_no_external_resources`

`assert_no_external_resources(page)` raises one `AssertionError` listing
every problem, or returns `None`.

**URL attributes.** On any element, each of `src`, `href`, `xlink:href`,
`srcset`, `action`, `formaction`, `poster` and `data` must be empty or a
`#fragment`, after stripping control characters and spaces from both ends.
A valueless attribute counts as empty. Each candidate in `srcset` is judged
by its first token. An `href` on an `a` may also be a relative link: no
scheme, no leading slash or backslash, no query, and no space or control
character inside. The page rules allow `a` and `href` for exactly that: the
index links each site page, and a view links a leaf's anchor, only by
relative links.

| Passes | Fails |
|---|---|
| `#leaf`, `#leaf%2D1`, an empty value | `https:` or `http:` anywhere |
| `other.html#leaf-1` on an `a` | a protocol-relative or `file:` URL |
| `sites/other.html#leaf-1` on an `a` | `data:`, `blob:` or `javascript:` |
| `../index.html` on an `a` | a root-relative link such as `/x.html` |
| | a link with a query, or a backslash |
| | a relative `src`, such as `a.png` |

**Meta, `@import` and `url()`.** A `meta` with an `http-equiv` other than
the CSP fails (a refresh, for one). `@import` fails in a `style` element or a
`style` attribute; a `style` element's text is judged whole, up to its end
tag, even when child tags inside an `svg` split it, and a self-closing
`<style/>` opens one, as in a browser. `url()` fails in a
`style` element and in any attribute value unless its argument is a
fragment, as in `url(#arrow)`.

**The head.** The page needs one `<meta charset="utf-8">`, directly followed
(only ASCII whitespace between) by the page's CSP meta. Any other character,
a no-break space among them, opens the body in a browser. When the page has a
`head` tag, the charset meta opens it. With or without one, no element other
than `html` and `head`, no comment and no text comes before the charset meta,
so a browser cannot meet a body element before the CSP. The CSP meta is
`CSP_META`, verbatim:

```html
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
```

The CSP guards a browser only. It allows inline script, because a page's
own script is inline, so escaping is still the defence; under a test, this
checker is the guard.

## How a view's tests use them

1. Build the page from records that hold every `HOSTILE` string.
2. `data = assert_page_safe(text, planted)`: the page breaks no rule, and the
   planted values reached the data.
3. `assert_no_external_resources(text)`.
4. Assert on `data`, which is what the page's own script will read.

The two committed pages in `tests/page_script/known_bad/` show each checker
failing on a value written raw, in element and in attribute context. Every
other test page is built by the test at run time and never committed: the
commit check refuses an HTML file anywhere else.

## Page-script harness

The harness loads a page into [jsdom](https://github.com/jsdom/jsdom), runs
its own scripts, and lets a test drive it and look at what it built. It is
`tests/page_script/harness.py`, which starts a Node.js child running
`tests/page_script/driver.js` and talks to it in JSON lines. A test opens a
page with `open_page`, works it with `drive`, reads it with `query`, and
checks what its script built with `planted_nodes`.

- `open_page(path)` is a context manager: it loads the page, waits for its
  load event and one settle step, and yields a page. The page's scripts run;
  nothing else it names is loaded. jsdom is no sandbox: a page's script runs
  with the test process's rights, so the harness is only for pages Gantry's
  own templates built, never for a page from outside the repository.
- `page.drive(control, value)` sets a control the way a user would (see
  [Control convention](#control-convention)), then settles.
- `page.query(selector)` returns the matching elements, in document order,
  each with its `tag`, `attrs`, `classes` and `text`. The `tag` is lower
  case; attribute names are as the live DOM holds them, so an SVG name keeps
  its case, such as `viewBox`.
- `page.planted_nodes(planted, rules=None)` reports three lists:
  - `created`: each element or attribute a planted value built, because the
    page wrote it as markup. The harness parses each value inertly, as markup
    and inside a double-quoted, single-quoted and unquoted attribute, and
    looks for what that parse builds in the live document.
  - `violations`: each break of the page rules in the live document, in the
    same shapes as `page_findings` (`rule`, `tag`, `attr`).
  - `unrendered`: each planted value that is in neither an attribute value
    nor any text outside `script`, `style` and `template`, and built nothing
    either: the page dropped it.

  `rules=` works as it does for the static checkers: `check_rules` checks it,
  and `RULES` is read at call time when it is left out. On each kind of
  break a page writes statically, and on both known-bad pages, the
  harness's `violations` and `page_findings` agree, and a test holds them
  equal; [Limits](#limits) lists the markup where they differ.
- `run_function(path, name, args=())` loads the page in a fresh window and
  calls the function at a dotted name, such as `gantryTest.pairs`, with
  `args` rebuilt as page values. The name resolves one own property at a
  time from `window`, so a top-level `const`, `let` or `class` is out of
  reach: a view assigns `globalThis.<name>`, or uses `function` or `var`.
  The return value comes back as JSON would carry it, with a `Map` as a list
  of `[key, value]` pairs in its order; `undefined`, a function, a `Set` or a
  non-finite number fails.

Two errors keep the page's failures apart from the harness's own:

- `PageScriptError`: a script error at load or in a listener (a thrown
  value of any kind, or a promise the page leaves rejected), a use of
  something jsdom does not implement, a refused network call or timer, a
  missing or disabled control or value, or a `run_function` name that is not
  defined, is not a function, throws or returns what JSON cannot carry;
- `HarnessError`: Node or jsdom is not ready, `package.json` pins no jsdom,
  the driver could not start, exited or sent no reply within `REPLY_TIMEOUT`
  seconds, the reply broke the protocol, or a selector is invalid. A new
  driver first gets `START_TIMEOUT` seconds to load jsdom, so a short
  `REPLY_TIMEOUT` times the page, not the load.

The harness loads no subresource: a `script src`, a stylesheet or a frame
the page names is never fetched, and `fetch`, `XMLHttpRequest`, `WebSocket`
and `EventSource` throw. jsdom does not enforce the CSP, so
`assert_no_external_resources` stays the guard against a page that would
load one in a browser.

## Control convention

A view's interactive controls carry `data-control`, with one of three kinds:

- a `select[data-control]`: `drive` picks the option with the value, then
  fires a bubbling `change`;
- a `button[data-control][data-value]`, one per value: `drive` clicks the
  one whose `data-value` is the value;
- a `data-control-items` container whose items carry `data-item`: `drive`
  fires a bubbling `focusin` on the first item whose `data-item` is the
  value.

```html
<select data-control="lanes"><option value="1">1</option><option value="2">2</option></select>
<button data-control="zoom" data-value="wave">wave</button>
<button data-control="zoom" data-value="all">all</button>
<svg data-control="detail" data-control-items><g data-item="leaf-1"></g></svg>
```

The page listens with delegated listeners on `document`, never inline
handlers, which the page rules refuse. A control name with no match, with
matches of more than one kind, or of none of these kinds, a value it does
not offer, and a disabled `select`, option or button are each a
`PageScriptError`.

## Node.js

Node.js 24 is the supported development version, at Node.js 24.15.0 or
later, which jsdom 30.1.3 needs. Installed Gantry never needs Node.js; only
these tests do. `tests/page_script/package.json` pins jsdom exactly and its
lock is committed, so every machine installs the same tree:

```
npm ci --prefix tests/page_script
```

`node_modules` is never committed; every fresh checkout or worktree runs
`npm ci --prefix tests/page_script` again. Before Node.js 24 reaches its end
of life, the major moves to the next LTS line, with the jsdom pin and the
lock, in a change of its own. A bump regenerates the lock and runs the
commit check on it again; a hit inside an integrity hash takes an
exemption line, re-keyed at each bump.

## Gate

Each Node test asks for the `node` fixture, which calls `require_node`. It
finds one of four states:

| State | Locally | Under `CI` |
|---|---|---|
| no `node` on `PATH` | skips | fails |
| jsdom not installed | skips | fails |
| jsdom installed at another version than the pin | skips | fails |
| ready | runs | runs |

A `package.json` that cannot be read or pins no jsdom is a `HarnessError`,
not a skip.

The reasons, verbatim (the CI failure adds a note that `CI` is set):

```
node is not on PATH
jsdom is not installed: run npm ci --prefix tests/page_script
jsdom <found> is installed, but package.json pins <pinned>: run npm ci --prefix tests/page_script
```

`CI` counts as set when it is not empty. GitHub sets it, and the test
workflow installs Node.js 24 and runs `npm ci --prefix tests/page_script` on
every leg, so a CI run can never pass on skipped Node tests. A test runs the
page-checker module with an empty `PATH` to show both behaviours end to end.

## How a view's tests use the harness

1. Ask for the `node` fixture, which calls `require_node`.
2. Write the page built from `HOSTILE` records, then `with open_page(path)
   as page:`.
3. `page.drive(...)` through the controls the view offers.
4. `page.query(...)` to assert on what the page shows.
5. `page.planted_nodes(HOSTILE)`: `created`, `violations` and `unrendered`
   are all empty.
6. `run_function(path, ...)` for a pure function the page's script exposes.

The committed page `tests/page_script/known_bad/raw_sink.html` writes a
`HOSTILE` value through `innerHTML`: both static checkers pass it, and the
harness reports the `img` and its handler it built.

## Limits

- A script sink such as `innerHTML` is invisible to a static read: a page
  whose own script writes markup passes. The
  [page-script harness](#page-script-harness) covers it.
- The harness runs no timer: `setTimeout`, `setInterval` and
  `requestAnimationFrame` throw, so a page that needs one fails closed with
  a `PageScriptError`. Promises still settle before each reply.
- On a page opened from a file, `localStorage` and `sessionStorage` throw,
  so a page that touches them is a `PageScriptError`. `ResizeObserver`,
  `IntersectionObserver` and a canvas `getContext` are missing or not
  implemented in jsdom, and fail the same way.
- jsdom does no layout: sizes and positions are zero.
- A page's console output is discarded.
- jsdom does not enforce the CSP.
- `drive` fires only `change`, `click` and `focusin`.
- The harness's `violations` and `page_findings` differ in detail on some
  statically written markup, though both fail the page: an element inside a
  `template` is reported only by `page_findings`, since the live document
  keeps template content apart; a valueless `<script id>` has the id `""`
  live and `None` statically; and `<image>` is `img` live.
- CSS escapes (a backslash escape inside `url` or `@import`) and strings in
  `image-set()` are not seen.
- Only the URL attributes listed above are checked. Others (`srcdoc`,
  `background`, `ping`, an svg `set`) are not seen; the page rules keep
  them out, so adding one to `allowed_attrs` needs a URL rule with it.
- A self-closing own-id `<script id="gantry-page"/>` passes, though a
  browser runs the text after it as that script. Only a template can
  write one; a record value cannot.
- Which element holds an own id is not checked: an element other than a
  script may carry `id="gantry-data"`, and `getElementById` would then
  find it before the data block.
- `title` and `textarea` text is raw text on newer Python versions and
  markup on 3.11, so a value written raw inside a `title` can give
  different findings on different CI versions.
- A record value holding `url(` in an attribute fails the check, even when
  it is only text: the check fails closed.
- `html.parser` is not an HTML5 parser, and its handling of malformed markup
  varies between Python versions. CI runs the oldest and newest supported
  versions.
