"""normalize: clipboard text -> Target (accepted path / URL notations)."""

from pathlib import Path

import pytest

from src.normalize import Target, normalize

HOME = Path(r"C:\Users\me")


def path_of(text: str) -> Target:
    target = normalize(text, HOME)
    assert target is not None, text
    assert target.kind == "path"
    return target


@pytest.mark.parametrize(
    "text, expected",
    [
        (r"C:\Users\me\knowledge\a.md", r"C:\Users\me\knowledge\a.md"),
        ("C:/Users/me/knowledge/a.md", r"C:\Users\me\knowledge\a.md"),
        ("~/knowledge/a.md", r"C:\Users\me\knowledge\a.md"),
        ("~\\knowledge\\a.md", r"C:\Users\me\knowledge\a.md"),
        ("/c/Users/me/knowledge/a.md", r"C:\Users\me\knowledge\a.md"),
        ("/mnt/c/Users/me/knowledge/a.md", r"C:\Users\me\knowledge\a.md"),
        ("file:///C:/Users/me/knowledge/a%20b.md", r"C:\Users\me\knowledge\a b.md"),
        ("file://localhost/C:/Users/me/a.md", r"C:\Users\me\a.md"),
        (
            "~/knowledge/_inbox/2026-10-03-x/2026-10-03-0815-[report]-slug.md",
            r"C:\Users\me\knowledge\_inbox\2026-10-03-x\2026-10-03-0815-[report]-slug.md",
        ),
        ("~/knowledge/日本語ノート.md", r"C:\Users\me\knowledge\日本語ノート.md"),
        (r"C:\Program Files\app\readme.txt", r"C:\Program Files\app\readme.txt"),
        ("~/knowledge", r"C:\Users\me\knowledge"),
        ("~", r"C:\Users\me"),
    ],
)
def test_path_notations(text, expected):
    target = path_of(text)
    assert target.path == Path(expected)
    assert target.line is None


@pytest.mark.parametrize(
    "text",
    [
        "`~/knowledge/a.md`",
        '"~/knowledge/a.md"',
        "'~/knowledge/a.md'",
        "「~/knowledge/a.md」",
        "<~/knowledge/a.md>",
        "(~/knowledge/a.md)",
        "  ~/knowledge/a.md  \r\n",
        "~/knowledge/a.md。",
        "~/knowledge/a.md、",
        "~/knowledge/a.md.",
        "`~/knowledge/a.md`。",
    ],
)
def test_wrappers_and_trailing_punctuation_are_stripped(text):
    assert path_of(text).path == Path(r"C:\Users\me\knowledge\a.md")


@pytest.mark.parametrize(
    "text, line",
    [
        ("~/src/app.py:42", 42),
        ("~/src/app.py:42:7", 42),
        ("~/src/app.py:42-50", 42),
        ("~/src/app.py#L42", 42),
        ("`~/src/app.py:42`", 42),
        (r"C:\Users\me\src\app.py:3", 3),
    ],
)
def test_line_suffix(text, line):
    target = path_of(text)
    assert target.path == Path(r"C:\Users\me\src\app.py")
    assert target.line == line


def test_terminal_wrapped_path_is_joined():
    text = "C:\\Users\\me\\knowledge\\_inbox\\2026-10-03-x\\2026-10-03-0815-[re\n  port]-slug.md"
    assert path_of(text).path == Path(
        r"C:\Users\me\knowledge\_inbox\2026-10-03-x\2026-10-03-0815-[report]-slug.md"
    )


def test_unc_path_is_returned_for_policy_to_reject():
    target = path_of(r"\\server\share\a.md")
    assert str(target.path).startswith("\\\\server")


def test_file_uri_with_host_becomes_unc():
    target = path_of("file://server/share/a.md")
    assert str(target.path).startswith("\\\\server")


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/a?b=1",
        "http://example.com",
        "`https://example.com/a`",
        "https://example.com/a。",
        "<https://example.com/a>",
    ],
)
def test_urls(text):
    target = normalize(text, HOME)
    assert target is not None
    assert target.kind == "url"
    assert target.url.rstrip("/") in (
        "https://example.com/a?b=1",
        "http://example.com",
        "https://example.com/a",
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "hello world",
        "see ~/knowledge/a.md for details",
        "src/app.py",
        "./app.py:12",
        "app.py",
        "ftp://example.com/a",
        "javascript:alert(1)",
        "https://example.com/a b",
        "C:",
        "x" * 5000,
    ],
)
def test_rejects_non_targets(text):
    assert normalize(text, HOME) is None


def test_none_input():
    assert normalize(None, HOME) is None


@pytest.mark.parametrize(
    "text",
    [
        "http://a\nhttp://b",
        "~/knowledge/a.md\n~/knowledge/b.md",
        "C:\\Users\\me\\a.md\nC:\\Users\\me\\b.md",
        "~/knowledge/a\n/c/Users/me/b.md",
        "https://example.com/a\nfile:///C:/x",
    ],
)
def test_multiple_tokens_on_separate_lines_are_rejected(text):
    assert normalize(text, HOME) is None


def test_wrapped_url_is_joined():
    target = normalize("https://example.com/very/long\n  /path?q=1", HOME)
    assert target.kind == "url"
    assert target.url == "https://example.com/very/long/path?q=1"


def test_file_uri_line_fragment():
    target = path_of("file:///C:/Users/me/a.md#L10")
    assert target.path == Path(r"C:\Users\me\a.md")
    assert target.line == 10
