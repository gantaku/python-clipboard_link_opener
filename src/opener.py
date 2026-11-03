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
# Linux: HTML goes to Chrome by name, not to whatever claimed text/html (ChatGPT did).
HTML_SUFFIXES = (".html", ".htm", ".xhtml")
BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
TEXT_EDITOR = "gedit"


@dataclass(frozen=True)
class LaunchPlan:
    # "shell": os.startfile(arg) / xdg-open arg; "exec": subprocess.Popen(arg) without a shell;
    # Linux only: "reveal": select arg in the file manager over D-Bus, else open its folder;
    # "browser": open the URI arg in Chrome; "editor": gedit with the arguments in arg
    kind: Literal["shell", "exec", "reveal", "browser", "editor"]
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
    if not IS_WINDOWS:
        posix = _plan_posix_file(path, target.path.suffix.lower(), target.line, vaults)
        if posix is not None:
            return posix
    if target.line is not None:
        # A URI instead of code.cmd: batch files re-parse arguments (& | ^).
        uri_path = _uri_path(path)
        separator = "" if uri_path.startswith("/") else "/"
        return LaunchPlan("shell", f"vscode://file{separator}{uri_path}:{target.line}")
    if target.path.suffix.lower() == ".md" and _in_any(path, vaults):
        return LaunchPlan("shell", "obsidian://open?path=" + quote(path, safe=""))
    return LaunchPlan("shell", path)


def _plan_posix_file(
    path: str, suffix: str, line: int | None, vaults: tuple[Path, ...]
) -> LaunchPlan | None:
    # Markdown never goes to VS Code: Obsidian inside a vault (the line is dropped),
    # else a plain text editor, since Obsidian cannot open files outside its vaults.
    if suffix == ".md":
        if _in_any(path, vaults):
            return LaunchPlan("shell", "obsidian://open?path=" + quote(path, safe=""))
        return LaunchPlan("editor", (path,) if line is None else (f"+{line}", path))
    if suffix in HTML_SUFFIXES and line is None:
        return LaunchPlan("browser", "file://" + quote(path, safe="/"))
    return None


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
    elif plan.kind == "browser":
        _spawn((_first_command(BROWSERS), plan.arg))
    elif plan.kind == "editor":
        _spawn((_system_command(TEXT_EDITOR), *plan.arg))
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


def _first_command(names: tuple[str, ...]) -> str:
    # No fallback to xdg-open: that is the handler which picked the wrong app.
    for name in names:
        try:
            return _system_command(name)
        except OSError:
            continue
    raise OSError(f"none of {', '.join(names)} found in {SYSTEM_BIN_PATH}")


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
