# Page safety checkers

Gantry's pages put project text, written by people, into HTML. A title that
holds markup must show as text, never run as script, and a page must never
load anything from outside itself. Two static checkers in
`tests/page_safety.py` hold every view's tests to that:

- `page_findings` and `assert_page_safe` check a page against the **page
  rules** and the embedded-data contract;
- `assert_no_external_resources` checks that nothing in the page can load a
  resource, and that the page's own CSP meta comes first in the head.

Both read the page text with one standard-library `html.parser` pass. They
are test helpers, not part of the installed package: a view's tests import
them as `page_safety`, the way they import `helpers_config`.

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
the browser harness. It returns each list as a frozenset, and it raises
`ValueError` naming the defect when the value is not an object, the keys are
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

Inside an `svg`, the children of `title` and `style` are read as markup, the
way a browser reads them there, so a raw value in an SVG tooltip is found.

`assert_page_safe(page, planted, rules=None)` lists every finding, then
checks the embedded data:

- the page holds exactly one `<script type="application/json"
  id="gantry-data">` (both attributes exact);
- its text is JSON (`NaN` and `Infinity` are refused);
- every planted value is in the data, as a string value or an object key, at
  any depth.

It raises one `AssertionError` listing every problem, each with its line, or
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
character inside.

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
`style` attribute. `url()` fails in a `style` element and in any attribute
value unless its argument is a fragment, as in `url(#arrow)`.

**The head.** The page needs one `<meta charset="utf-8">`, directly followed
(only ASCII whitespace between) by the page's CSP meta. Any other character,
a no-break space among them, opens the body in a browser. When the page has a `head`
tag, the charset meta opens it. With or without one, no element other than
`html` and `head`, no comment and no text comes before the charset meta, so a
browser cannot meet a body element before the CSP. The CSP meta is
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

## Limits

- A script sink such as `innerHTML` is invisible to a static read: a page
  whose own script writes markup passes. The browser harness covers it.
- CSS escapes (a backslash escape inside the word `url`) and strings in
  `image-set()` are not seen.
- Only the URL attributes listed above are checked; the page rules keep the
  other URL-bearing attributes out.
- A record value holding `url(` in an attribute fails the check, even when
  it is only text: the check fails closed.
- `html.parser` is not an HTML5 parser, and its handling of malformed markup
  varies between Python versions. CI runs the oldest and newest supported
  versions.
