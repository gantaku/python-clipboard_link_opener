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

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
MAX_LINK_DEPTH = 8

Action = Literal["open", "folder", "reveal", "url", "reject"]


@dataclass(frozen=True)
class Policy:
    allowed_roots: tuple[Path, ...]
    open_urls: bool = True
    reveal_extensions: frozenset[str] = field(default=DEFAULT_REVEAL_EXTENSIONS)
    # Lower-case exe names a copy must come from; empty means any app.
    source_apps: frozenset[str] = frozenset()


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
    network_check: Callable[[str], str | None] | None = None,
) -> Decision:
    network_check = network_check or network_hop
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

    hop = network_check(raw)
    if hop is not None:
        # Checked before realpath(): resolving would already contact the server.
        return Decision("reject", target, hop)

    resolved = _canonical(raw)
    if resolved.startswith("\\\\"):
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


def source_allowed(source: str | None, policy: Policy) -> bool:
    if not policy.source_apps:
        return True
    return source is not None and source.lower() in policy.source_apps


def network_hop(
    path: str,
    lstat: Callable = os.lstat,
    readlink: Callable[[str], str] = os.readlink,
    drive_type: Callable[[str], int] | None = None,
    _depth: int = 0,
) -> str | None:
    """Reason string if reaching ``path`` would touch the network, else None.

    Walks each component with lstat/readlink, which read the link itself and
    never follow it, so a link to a share is caught before anything contacts it.
    """
    drive_type = drive_type or _drive_type
    if _depth > MAX_LINK_DEPTH:
        return "too many links"
    full = os.path.abspath(path)
    if full.startswith(("\\\\", "//")):
        return "UNC paths are not allowed"
    drive, rest = os.path.splitdrive(full)
    if drive_type(drive + "\\") == DRIVE_REMOTE:
        return "network drives are not allowed"
    parts = [part for part in rest.split("\\") if part]
    current = drive + "\\"
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            attributes = getattr(lstat(current), "st_file_attributes", 0)
        except OSError:
            return None  # missing; the existence check rejects it later
        if not attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            continue
        try:
            link = _strip_nt_prefix(readlink(current))
        except OSError:
            continue  # reparse point that is not a link (e.g. OneDrive placeholder)
        if not os.path.isabs(link) and not link.startswith("\\\\"):
            link = os.path.join(os.path.dirname(current), link)
        remaining = os.path.join(link, *parts[index + 1 :])
        return network_hop(remaining, lstat, readlink, drive_type, _depth + 1)
    return None


def _strip_nt_prefix(link: str) -> str:
    for prefix in ("\\\\?\\UNC\\", "\\??\\UNC\\"):
        if link.upper().startswith(prefix):
            return "\\\\" + link[len(prefix) :]
    for prefix in ("\\\\?\\", "\\??\\"):
        if link.startswith(prefix):
            return link[len(prefix) :]
    return link


def _drive_type(root: str) -> int:
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(root)


def _canonical(path: str) -> str:
    # realpath: a junction inside an allowed root must not lead outside it.
    return os.path.realpath(os.path.abspath(path))


def _is_within(path: str, root: str) -> bool:
    p, r = os.path.normcase(path), os.path.normcase(root).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)
