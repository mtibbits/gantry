"""The full build: anchors and hrefs, reading and rendering every site, and
writing the pages and the index (#11)."""

import re

from gantry.links import leaf_anchor, leaf_href, page_href
from page_safety import HOSTILE

# --- anchors and hrefs ------------------------------------------------------------


def test_leaf_anchor_forms():
    assert leaf_anchor("Issue-7") == "leaf-Issue-7"
    assert leaf_anchor("2.1~9") == "leaf-2.1~9"
    assert leaf_anchor("a b<c>\"&'") == "leaf-a%20b%3Cc%3E%22%26%27"
    assert leaf_anchor("é") == "leaf-%C3%A9"
    assert leaf_anchor("%") == "leaf-%25"


def test_leaf_anchor_hostile():
    for text in HOSTILE:
        assert re.fullmatch(r"leaf-[A-Za-z0-9_.~%-]+", leaf_anchor(text)), text


def test_href_forms():
    assert leaf_href("harbor", "Issue-7", from_top=False) == "harbor.html#leaf-Issue-7"
    assert leaf_href("harbor", "Issue-7", from_top=True) == "sites/harbor.html#leaf-Issue-7"
    assert page_href("harbor", from_top=True) == "sites/harbor.html"
    assert page_href("harbor", from_top=False) == "harbor.html"
