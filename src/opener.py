"""Map a decision to a launch plan, and execute it."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from src.policy import Decision


@dataclass(frozen=True)
class LaunchPlan:
    # "shell": os.startfile(arg) / "exec": subprocess.Popen(arg) without a shell
    kind: Literal["shell", "exec"]
    arg: str | tuple[str, ...]


def plan_launch(decision: Decision, vaults: tuple[Path, ...]) -> LaunchPlan | None:
    target = decision.target
    if decision.action == "url":
        return LaunchPlan("shell", target.url)
    if decision.action == "reject" or target.path is None:
        return None

    path = str(target.path)
    if decision.action == "folder":
        return LaunchPlan("exec", (_explorer(), path))
    if decision.action == "reveal":
        return LaunchPlan("exec", (_explorer(), "/select,", path))
    if target.line is not None:
        # A URI instead of code.cmd: batch files re-parse arguments (& | ^).
        return LaunchPlan("shell", f"vscode://file/{_uri_path(path)}:{target.line}")
    if target.path.suffix.lower() == ".md" and _in_any(path, vaults):
        return LaunchPlan("shell", "obsidian://open?path=" + quote(path, safe=""))
    return LaunchPlan("shell", path)


def launch(plan: LaunchPlan) -> None:
    if plan.kind == "shell":
        os.startfile(plan.arg)  # noqa: S606 - target validated by policy
    else:
        subprocess.Popen(list(plan.arg), close_fds=True)  # noqa: S603


def _explorer() -> str:
    # Absolute path: a bare "explorer" is searched in the current directory first.
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")


def _uri_path(path: str) -> str:
    return quote(path.replace("\\", "/"), safe="/:")


def _in_any(path: str, roots: tuple[Path, ...]) -> bool:
    p = os.path.normcase(path)
    for root in roots:
        r = os.path.normcase(str(root)).rstrip("\\/")
        if p == r or p.startswith(r + os.sep):
            return True
    return False
