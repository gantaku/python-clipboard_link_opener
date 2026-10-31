"""Map a decision to a launch plan, and execute it."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from src.osinfo import IS_WINDOWS
from src.policy import Decision

# Only system directories: a bare name would also search the current directory and ~/.local/bin.
SYSTEM_BIN_PATH = "/usr/local/bin:/usr/bin:/bin"
FILE_MANAGER_TIMEOUT = 5.0


@dataclass(frozen=True)
class LaunchPlan:
    # "shell": os.startfile(arg) / xdg-open arg; "exec": subprocess.Popen(arg) without a shell;
    # "reveal" (Linux): select arg in the file manager over D-Bus, else open its folder
    kind: Literal["shell", "exec", "reveal"]
    arg: str | tuple[str, ...]


def plan_launch(decision: Decision, vaults: tuple[Path, ...]) -> LaunchPlan | None:
    target = decision.target
    if decision.action == "url":
        return LaunchPlan("shell", target.url)
    if decision.action == "reject" or target.path is None:
        return None

    path = str(target.path)
    if decision.action == "folder":
        if not IS_WINDOWS:
            return LaunchPlan("shell", path)
        return LaunchPlan("exec", (_explorer(), path))
    if decision.action == "reveal":
        if not IS_WINDOWS:
            return LaunchPlan("reveal", path)
        return LaunchPlan("exec", (_explorer(), "/select,", path))
    if target.line is not None:
        # A URI instead of code.cmd: batch files re-parse arguments (& | ^).
        uri_path = _uri_path(path)
        separator = "" if uri_path.startswith("/") else "/"
        return LaunchPlan("shell", f"vscode://file{separator}{uri_path}:{target.line}")
    if target.path.suffix.lower() == ".md" and _in_any(path, vaults):
        return LaunchPlan("shell", "obsidian://open?path=" + quote(path, safe=""))
    return LaunchPlan("shell", path)


def launch(plan: LaunchPlan) -> None:
    if not IS_WINDOWS:
        _launch_posix(plan)
    elif plan.kind == "shell":
        os.startfile(plan.arg)  # noqa: S606 - target validated by policy
    else:
        subprocess.Popen(list(plan.arg), close_fds=True)  # noqa: S603


def _launch_posix(plan: LaunchPlan) -> None:
    if plan.kind == "shell":
        _spawn((_system_command("xdg-open"), plan.arg))
    elif plan.kind == "reveal":
        if not _show_in_file_manager(plan.arg):
            _spawn((_system_command("xdg-open"), os.path.dirname(plan.arg)))
    else:
        _spawn(plan.arg)


def _show_in_file_manager(path: str) -> bool:
    # org.freedesktop.FileManager1.ShowItems selects the file without opening it.
    # The URI is percent-encoded, so it cannot break out of the GVariant string.
    uri = "file://" + quote(path, safe="/")
    try:
        gdbus = _system_command("gdbus")
        result = subprocess.run(  # noqa: S603
            [
                gdbus, "call", "--session",
                "--dest", "org.freedesktop.FileManager1",
                "--object-path", "/org/freedesktop/FileManager1",
                "--method", "org.freedesktop.FileManager1.ShowItems",
                f"['{uri}']", "",
            ],
            capture_output=True,
            timeout=FILE_MANAGER_TIMEOUT,
            close_fds=True,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _spawn(args: tuple[str, ...]) -> None:
    subprocess.Popen(  # noqa: S603
        list(args),
        close_fds=True,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _system_command(name: str) -> str:
    found = shutil.which(name, path=SYSTEM_BIN_PATH)
    if found is None:
        raise OSError(f"{name} not found in {SYSTEM_BIN_PATH}")
    return found


def _explorer() -> str:
    # Absolute path: a bare "explorer" is searched in the current directory first.
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")


def _uri_path(path: str) -> str:
    # A backslash is a separator only on Windows; on Linux it is part of a name.
    return quote(path.replace("\\", "/") if IS_WINDOWS else path, safe="/:")


def _in_any(path: str, roots: tuple[Path, ...]) -> bool:
    p = os.path.normcase(path)
    for root in roots:
        r = os.path.normcase(str(root)).rstrip("\\/")
        if p == r or p.startswith(r + os.sep):
            return True
    return False
