"""Decide whether a normalized target may be opened, and how."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from src.normalize import Target

# Opening these through the shell would run them; only reveal them in Explorer.
DEFAULT_REVEAL_EXTENSIONS = frozenset(
    {
        ".appinstaller", ".application", ".appref-ms", ".appx", ".appxbundle",
        ".bat", ".chm", ".cmd", ".com", ".cpl", ".diagcab", ".docm", ".dotm",
        ".exe", ".gadget", ".hlp", ".hta", ".inf", ".iqy", ".jar", ".jnlp", ".js",
        ".jse", ".library-ms", ".lnk", ".mht", ".mhtml", ".msc", ".msi", ".msix",
        ".msixbundle", ".msp", ".pif", ".pl", ".potm", ".ppam", ".ppsm", ".pptm",
        ".ps1", ".ps1xml", ".psd1", ".psm1", ".py", ".pyw", ".rb", ".rdp", ".reg",
        ".scf", ".scr", ".search-ms", ".searchconnector-ms", ".settingcontent-ms",
        ".sh", ".slk", ".theme", ".themepack", ".url", ".vb", ".vbe", ".vbs",
        ".website", ".ws", ".wsc", ".wsf", ".wsh", ".xlam", ".xll", ".xlsm",
        ".xltm",
    }
)  # fmt: skip

Action = Literal["open", "folder", "reveal", "url", "reject"]


@dataclass(frozen=True)
class Policy:
    allowed_roots: tuple[Path, ...]
    open_urls: bool = True
    reveal_extensions: frozenset[str] = field(default=DEFAULT_REVEAL_EXTENSIONS)


@dataclass(frozen=True)
class Decision:
    action: Action
    target: Target
    reason: str = ""


def decide(
    target: Target,
    policy: Policy,
    exists: Callable[[str], bool] = os.path.exists,
    is_dir: Callable[[str], bool] = os.path.isdir,
) -> Decision:
    if target.kind == "url":
        if policy.open_urls:
            return Decision("url", target)
        return Decision("reject", target, "url opening is disabled")

    raw = str(target.path)
    if raw.startswith(("\\\\", "//")):
        # Touching a UNC path can leak NTLM credentials; never stat it.
        return Decision("reject", target, "UNC paths are not allowed")

    if ":" in raw[2:]:
        # NTFS alternate data streams ("x.exe::$DATA") hide the real extension.
        return Decision("reject", target, "alternate data streams are not allowed")

    resolved = _canonical(raw)
    if resolved.startswith("\\\\"):
        # A junction/symlink pointing at a share.
        return Decision("reject", target, "UNC paths are not allowed")
    if not any(
        _is_within(resolved, _canonical(str(root))) for root in policy.allowed_roots
    ):
        return Decision("reject", target, "outside allowed roots")
    if not exists(resolved):
        return Decision("reject", target, "does not exist")

    clean = Target(kind="path", path=Path(resolved), line=target.line)
    if is_dir(resolved):
        return Decision("folder", clean)
    # With a line number the file goes to the editor (vscode:// URI), which never runs it.
    # Windows ignores trailing dots/spaces ("run.bat." runs run.bat).
    suffix = Path(Path(resolved).name.rstrip(". ")).suffix.lower()
    if target.line is None and suffix in policy.reveal_extensions:
        return Decision("reveal", clean, "executable type")
    return Decision("open", clean)


def _canonical(path: str) -> str:
    # realpath: a junction inside an allowed root must not lead outside it.
    return os.path.realpath(os.path.abspath(path))


def _is_within(path: str, root: str) -> bool:
    p, r = os.path.normcase(path), os.path.normcase(root).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)
