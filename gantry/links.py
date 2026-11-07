"""Leaf anchors and page hrefs: the one encoding every view links with (#11).

A leaf's anchor is `leaf-` and its key percent-encoded with nothing kept
safe, so an anchor holds no whitespace or markup. A worksite page is
`sites/<name>.html` under the output folder, and every href is relative to
the page that holds it: `from_top=False` between pages in `sites/`,
`from_top=True` from a page at the output folder's top level, such as the
index. docs/worksite-page.md states the forms.
"""

from urllib.parse import quote


def leaf_anchor(key: str) -> str:
    return "leaf-" + quote(key, safe="")


def page_href(site: str, *, from_top: bool) -> str:
    return ("sites/" if from_top else "") + quote(site, safe="") + ".html"


def leaf_href(site: str, key: str, *, from_top: bool) -> str:
    return page_href(site, from_top=from_top) + "#" + leaf_anchor(key)
