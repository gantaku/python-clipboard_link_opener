"""policy: decide whether / how a normalized target may be opened."""

from pathlib import Path

import pytest

from src.normalize import Target
from src.policy import Policy, decide


@pytest.fixture
def root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "note.md").write_text("x", encoding="utf-8")
    (allowed / "run.bat").write_text("x", encoding="utf-8")
    (allowed / "Script.PS1").write_text("x", encoding="utf-8")
    (allowed / "sub").mkdir()
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    return allowed


def make_policy(root: Path, open_urls: bool = True) -> Policy:
    return Policy(allowed_roots=(root,), open_urls=open_urls)


def path_target(path: Path, line=None) -> Target:
    return Target(kind="path", path=path, line=line)


def test_existing_file_under_root_opens(root):
    decision = decide(path_target(root / "note.md"), make_policy(root))
    assert decision.action == "open"


def test_directory_under_root_opens_as_folder(root):
    decision = decide(path_target(root / "sub"), make_policy(root))
    assert decision.action == "folder"


def test_root_itself_is_allowed(root):
    assert decide(path_target(root), make_policy(root)).action == "folder"


def test_root_match_is_case_insensitive(root):
    upper = Path(str(root).upper()) / "NOTE.MD"
    assert decide(path_target(upper), make_policy(root)).action == "open"


def test_missing_file_is_rejected(root):
    decision = decide(path_target(root / "missing.md"), make_policy(root))
    assert decision.action == "reject"
    assert "exist" in decision.reason


def test_file_outside_root_is_rejected(root):
    decision = decide(path_target(root.parent / "outside.md"), make_policy(root))
    assert decision.action == "reject"
    assert "root" in decision.reason


def test_dotdot_escape_is_rejected(root):
    sneaky = root / ".." / "outside.md"
    assert decide(path_target(sneaky), make_policy(root)).action == "reject"


def test_sibling_with_common_prefix_is_rejected(root, tmp_path):
    sibling = tmp_path / "allowed-evil"
    sibling.mkdir()
    (sibling / "a.md").write_text("x", encoding="utf-8")
    assert decide(path_target(sibling / "a.md"), make_policy(root)).action == "reject"


@pytest.mark.parametrize("name", ["run.bat", "Script.PS1"])
def test_executables_are_only_revealed(root, name):
    assert decide(path_target(root / name), make_policy(root)).action == "reveal"


def test_unc_is_rejected_without_touching_disk(root):
    calls = []

    def exists(p):
        calls.append(p)
        return True

    decision = decide(
        path_target(Path(r"\\server\share\a.md")), make_policy(root), exists=exists
    )
    assert decision.action == "reject"
    assert calls == []


def test_url_allowed_when_enabled(root):
    target = Target(kind="url", url="https://example.com")
    assert decide(target, make_policy(root, open_urls=True)).action == "url"


def test_url_rejected_when_disabled(root):
    target = Target(kind="url", url="https://example.com")
    assert decide(target, make_policy(root, open_urls=False)).action == "reject"


def test_line_is_kept_for_files(root):
    decision = decide(path_target(root / "note.md", line=3), make_policy(root))
    assert decision.action == "open"
    assert decision.target.line == 3


def test_executable_with_line_opens_in_editor(root):
    # With a line number the opener uses a vscode:// URI, which never runs the file.
    decision = decide(path_target(root / "run.bat", line=3), make_policy(root))
    assert decision.action == "open"


@pytest.mark.parametrize("suffix", ["::$DATA", ":stream", ".", " ", ". ."])
def test_ads_and_trailing_dots_cannot_bypass_reveal(root, suffix):
    decision = decide(path_target(Path(str(root / "run.bat") + suffix)), make_policy(root))
    assert decision.action in ("reveal", "reject")


@pytest.mark.parametrize("name", ["a.chm", "a.docm", "a.xlsm", "a.rdp", "a.search-ms", "a.appinstaller"])
def test_more_dangerous_types_are_revealed(root, name):
    (root / name).write_text("x", encoding="utf-8")
    assert decide(path_target(root / name), make_policy(root)).action == "reveal"


def test_link_escaping_root_is_rejected(root, tmp_path):
    import os
    import subprocess

    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "a.md").write_text("x", encoding="utf-8")
    link = root / "jump"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    try:
        assert decide(path_target(link / "a.md"), make_policy(root)).action == "reject"
    finally:
        os.rmdir(link)


def test_source_filter():
    from src.policy import source_allowed

    policy = Policy(allowed_roots=(), source_apps=frozenset({"windowsterminal.exe"}))
    assert source_allowed("WindowsTerminal.exe", policy)
    assert not source_allowed("chrome.exe", policy)
    assert not source_allowed(None, policy)


def test_empty_source_list_allows_any_app():
    from src.policy import source_allowed

    policy = Policy(allowed_roots=(), source_apps=frozenset())
    assert source_allowed("chrome.exe", policy)
    assert source_allowed(None, policy)
