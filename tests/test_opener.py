"""opener: map a decision to a concrete launch plan (pure)."""

from pathlib import Path

from src.normalize import Target
from src.opener import LaunchPlan, plan_launch
from src.policy import Decision

VAULTS = (Path(r"C:\Users\me\knowledge"),)


def decision(action, path=None, line=None, url=None):
    kind = "url" if url else "path"
    return Decision(
        action=action, target=Target(kind=kind, path=path, line=line, url=url)
    )


def test_url_opens_in_default_browser():
    plan = plan_launch(decision("url", url="https://example.com"), VAULTS)
    assert plan == LaunchPlan(kind="shell", arg="https://example.com")


def test_folder_opens_in_explorer():
    plan = plan_launch(decision("folder", Path(r"C:\Users\me\knowledge")), VAULTS)
    assert plan.kind == "exec"
    assert plan.arg[0].lower().endswith("explorer.exe")
    assert plan.arg[1:] == (r"C:\Users\me\knowledge",)


def test_executable_is_revealed_in_explorer():
    plan = plan_launch(decision("reveal", Path(r"C:\Users\me\work\run.bat")), VAULTS)
    assert plan.kind == "exec"
    assert plan.arg[0].lower().endswith("explorer.exe")
    assert plan.arg[1:] == ("/select,", r"C:\Users\me\work\run.bat")


def test_line_number_opens_vscode_uri():
    plan = plan_launch(
        decision("open", Path(r"C:\Users\me\work\my app.py"), line=42), VAULTS
    )
    assert plan == LaunchPlan(
        kind="shell", arg="vscode://file/C:/Users/me/work/my%20app.py:42"
    )


def test_markdown_in_vault_opens_obsidian():
    path = Path(r"C:\Users\me\knowledge\_inbox\x\2026-[report]-a b.md")
    plan = plan_launch(decision("open", path), VAULTS)
    assert plan.kind == "shell"
    assert plan.arg.startswith("obsidian://open?path=")
    assert "%5Breport%5D" in plan.arg
    assert " " not in plan.arg


def test_markdown_vault_match_is_case_insensitive():
    path = Path(r"c:\users\ME\Knowledge\a.MD")
    assert plan_launch(decision("open", path), VAULTS).arg.startswith("obsidian://")


def test_markdown_outside_vault_uses_default_app():
    path = Path(r"C:\Users\me\workspaces\README.md")
    assert plan_launch(decision("open", path), VAULTS) == LaunchPlan(
        kind="shell", arg=str(path)
    )


def test_markdown_with_line_prefers_vscode():
    path = Path(r"C:\Users\me\knowledge\a.md")
    assert plan_launch(decision("open", path, line=3), VAULTS).arg.startswith(
        "vscode://"
    )


def test_other_file_uses_default_app():
    path = Path(r"C:\Users\me\work\a.pdf")
    assert plan_launch(decision("open", path), VAULTS) == LaunchPlan(
        kind="shell", arg=str(path)
    )


def test_reject_has_no_plan():
    assert plan_launch(decision("reject", Path(r"C:\x")), VAULTS) is None


def test_explorer_is_absolute_path(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    plan = plan_launch(decision("folder", Path(r"C:\x")), VAULTS)
    assert plan.arg[0] == r"C:\Windows\explorer.exe"
