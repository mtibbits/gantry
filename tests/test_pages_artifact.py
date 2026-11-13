"""tools/pages_artifact.py: the Pages artifact, demo/ plus the holding page (#45)."""

import pytest

import pages_artifact
from helpers_config import page_links, written_bytes
from page_safety import CSP_META, assert_no_external_resources

URL = "https://github.com/example-owner/gantry"


def fake_demo(tmp_path):
    demo = tmp_path / "demo-src"
    (demo / "sites").mkdir(parents=True)
    (demo / "index.html").write_bytes(b"<p>index</p>\n")
    (demo / "sites" / "one.html").write_bytes(b"<p>one</p>\n")
    return demo


def test_holding_page_loads_nothing():
    page = pages_artifact.holding_page(URL)
    assert page.count(f'href="{URL}"') == 1
    assert_no_external_resources(page.replace(f'href="{URL}"', 'href="repo.html"'))
    assert page_links(page) == ["demo/", URL]
    assert page.startswith('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' + CSP_META + "\n")
    assert "<p>The Gantry website is coming.</p>" in page
    assert "\r" not in page


def test_stage_layout(tmp_path):
    demo = fake_demo(tmp_path)
    stage = tmp_path / "stage"
    assert pages_artifact.main([str(demo), str(stage), "--repo-url", URL]) == 0
    expected = {f"demo/{k}": v for k, v in written_bytes(demo).items()}
    expected["index.html"] = pages_artifact.holding_page(URL).encode("utf-8")
    assert written_bytes(stage) == expected
    assert (stage / "demo" / "index.html").is_file()  # so href="demo/" lands


def test_stage_into_empty_folder(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    assert pages_artifact.main([str(fake_demo(tmp_path)), str(stage), "--repo-url", URL]) == 0
    assert (stage / "index.html").is_file()


def stage_not_empty(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "old.txt").write_bytes(b"old\n")
    return fake_demo(tmp_path), stage, URL


def no_index(tmp_path):
    demo = fake_demo(tmp_path)
    (demo / "index.html").unlink()
    return demo, tmp_path / "stage", URL


def bad_url(url):
    return lambda tmp_path: (fake_demo(tmp_path), tmp_path / "stage", url)


REFUSALS = {
    "stage-not-empty": (stage_not_empty, "the stage folder is not empty"),
    "no-index": (no_index, "the demo folder holds no index.html"),
    "missing-demo": (lambda t: (t / "absent", t / "stage", URL), "the demo folder holds no index.html"),
    "bad-url-http": (bad_url("http://github.com/a/b"), "--repo-url is not an https repository URL"),
    "bad-url-quote": (bad_url('https://github.com/a/b"onclick="x'), "--repo-url is not an https repository URL"),
    "bad-url-path": (bad_url("https://github.com/a"), "--repo-url is not an https repository URL"),
    "bad-url-newline": (bad_url("https://github.com/a/b\n"), "--repo-url is not an https repository URL"),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_refusals(case, tmp_path, capsys):
    make, message = REFUSALS[case]
    demo, stage, url = make(tmp_path)
    before = written_bytes(stage) if stage.is_dir() else None
    assert pages_artifact.main([str(demo), str(stage), "--repo-url", url]) == 1
    assert capsys.readouterr().err.splitlines() == [f"pages_artifact: {message}"]
    assert (written_bytes(stage) if stage.is_dir() else None) == before


@pytest.mark.parametrize("argv", [
    pytest.param(["demo", "stage"], id="no-repo-url"),
    pytest.param(["demo", "--repo-url", URL], id="no-stage"),
])
def test_usage(argv):
    with pytest.raises(SystemExit) as raised:
        pages_artifact.main(argv)
    assert raised.value.code == 2
