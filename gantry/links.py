"""Leaf anchors and page hrefs: the one encoding every view links with (#11).

A leaf's anchor is `leaf-` and its key percent-encoded with nothing kept
safe, so an anchor holds no whitespace or markup. A worksite page is
`sites/<name>.html` under the output folder, and every href is relative to
the page that holds it: `from_top=False` between pages in `sites/`,
`from_top=True` from a page at the output folder's top level, such as the
index. docs/worksite-page.md states the forms.

id_href is the one rule for where a requirement ID links (#21): to the page
of the site whose namespace it is, when that is another site with a page in
this build, and never for br_namespace, which a site may share.
"""

from urllib.parse import quote


def leaf_anchor(key: str) -> str:
    return "leaf-" + quote(key, safe="")


def page_href(site: str, *, from_top: bool) -> str:
    return ("sites/" if from_top else "") + quote(site, safe="") + ".html"


def leaf_href(site: str, key: str, *, from_top: bool) -> str:
    return page_href(site, from_top=from_top) + "#" + leaf_anchor(key)


def id_href(rid, page_site: str, built, br_namespace: str) -> str | None:
    """The href of requirement ID `rid` on page_site's page, or None when it stays plain text."""
    ns = rid.namespace
    if ns == page_site or ns == br_namespace or ns not in built:
        return None
    return page_href(ns, from_top=False)
