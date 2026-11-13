"""tools/check_demo.py: the demo output check and the Pages source check (#45).

Every --pages-source input is hand-written bytes here, never a captured
response.
"""

import pytest

import check_demo

MUST = '; it must be "workflow" (Settings, Pages, Source: GitHub Actions)'
OK_LINE = 'check_demo: pages source: build_type is "workflow"'

PAGES_SOURCE = {
    "workflow": (b'{"url": "x", "build_type": "workflow", "source": {"branch": "main"}}', None),
    "legacy": (b'{"build_type": "legacy", "source": {"branch": "main", "path": "/"}}', 'build_type is "legacy"'),
    "null": (b'{"build_type": null}', "build_type is null"),
    "long": (b'{"build_type": "' + b"x" * 60 + b'"}', "build_type is " + ('"' + "x" * 60)[:40]),
    "not-found-body": (b'{"message": "Not Found", "status": "404"}', "the response has no build_type"),
    "empty": (b"", "the response is empty"),
    "whitespace": (b" \r\n\t\n", "the response is empty"),
    "not-json": (b"<html>Not Found</html>", "the response is not JSON"),
    "not-utf8": (b'{"build_type": "\xff"}', "the response is not JSON"),
    "json-list": (b'[{"build_type": "workflow"}]', "the response is not a JSON object"),
    "json-null": (b"null", "the response is not a JSON object"),
    "workflow-wrong-case": (b'{"build_type": "Workflow"}', 'build_type is "Workflow"'),
    "missing-file": (None, "cannot read the file"),
}


@pytest.mark.parametrize("case", list(PAGES_SOURCE))
def test_pages_source(case, tmp_path, capsys):
    data, problem = PAGES_SOURCE[case]
    file = tmp_path / "pages-source.json"
    if data is not None:
        file.write_bytes(data)
    rc = check_demo.main(["--pages-source", str(file)])
    out = capsys.readouterr()
    if problem is None:
        assert rc == 0
        assert out.out.splitlines() == [OK_LINE]
        assert out.err == ""
        assert check_demo.pages_source_problem(data) is None
    else:
        assert rc == 1
        assert out.err.splitlines() == [f"check_demo: pages source: {problem}{MUST}"]
        assert out.out == ""
        assert str(tmp_path) not in out.err
        if data is not None:
            assert check_demo.pages_source_problem(data) == problem + MUST


def test_pages_source_prints_nothing_else(tmp_path, capsys):
    file = tmp_path / "pages-source.json"
    file.write_bytes(b'{"build_type": "legacy", "html_url": "secret-looking-value"}')
    assert check_demo.main(["--pages-source", str(file)]) == 1
    assert "secret-looking-value" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    pytest.param([], id="neither"),
    pytest.param(["folder", "--pages-source", "file"], id="both"),
    pytest.param(["--pages-source", ""], id="empty-file"),
    pytest.param([""], id="empty-folder"),
    pytest.param(["--pages-source"], id="no-file"),
    pytest.param(["--unknown", "folder"], id="unknown-flag"),
    pytest.param(["a", "b"], id="two-folders"),
])
def test_usage(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        check_demo.main(argv)
    assert raised.value.code == 2
    assert "usage:" in capsys.readouterr().err
