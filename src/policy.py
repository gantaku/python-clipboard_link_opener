"""Decide whether a normalized target may be opened, and how."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from src.normalize import Target
from src.osinfo import IS_WINDOWS

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

# Linux adds its own launchers. The Windows list stays: xdg-open may hand .exe to Wine.
LINUX_REVEAL_EXTENSIONS = frozenset(
    {
        ".appimage", ".bash", ".bin", ".csh", ".deb", ".desktop", ".fish",
        ".flatpak", ".flatpakref", ".flatpakrepo", ".ksh", ".run", ".rpm",
        ".snap", ".zsh",
    }
)  # fmt: skip
BUILTIN_REVEAL_EXTENSIONS = (
    DEFAULT_REVEAL_EXTENSIONS
    if IS_WINDOWS
    else DEFAULT_REVEAL_EXTENSIONS | LINUX_REVEAL_EXTENSIONS
)

# Remote or userspace file systems. Any FUSE mount counts: sshfs, rclone, gvfs, ...
NETWORK_FS_TYPES = frozenset(
    {
        "9p", "afs", "autofs", "ceph", "cifs", "coda", "davfs", "gfs2",
        "glusterfs", "lustre", "ncpfs", "nfs", "nfs4", "ocfs2", "smb3", "smbfs",
        "sshfs",
    }
)  # fmt: skip
MOUNTINFO = "/proc/self/mountinfo"

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
MAX_LINK_DEPTH = 8

Action = Literal["open", "folder", "reveal", "url", "reject"]


@dataclass(frozen=True)
class Policy:
    allowed_roots: tuple[Path, ...]
    open_urls: bool = True
    reveal_extensions: frozenset[str] = field(default=BUILTIN_REVEAL_EXTENSIONS)
    # Lower-case exe names a copy must come from; empty means any app.
    source_apps: frozenset[str] = frozenset()
    # Linux: also open what is merely selected (X11 PRIMARY).
    watch_primary: bool = False


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
    executable_check: Callable[[str], bool] | None = None,
) -> Decision:
    network_check = network_check or (network_hop if IS_WINDOWS else posix_network_hop)
    executable_check = executable_check or (
        (lambda _path: False) if IS_WINDOWS else posix_looks_executable
    )
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
    if target.line is None and (
        suffix in policy.reveal_extensions or executable_check(resolved)
    ):
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


# --- Linux ---------------------------------------------------------------------


def posix_network_hop(
    path: str,
    lstat: Callable = os.lstat,
    readlink: Callable[[str], str] = os.readlink,
    mounts: list[tuple[str, str]] | None = None,
    _depth: int = 0,
) -> str | None:
    """Reason string if reaching ``path`` would touch a network or FUSE mount, else None.

    Each component's mount is checked before lstat() touches it, and links are
    read with readlink() instead of being followed.
    """
    if _depth > MAX_LINK_DEPTH:
        return "too many links"
    if path.startswith(("\\\\", "//")):
        return "network paths are not allowed"
    if mounts is None:
        try:
            mounts = read_mounts()
        except OSError as error:
            return f"cannot read the mount table: {error}"
    full = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    # Not normpath(): "link/.." must be resolved through the link, not lexically.
    parts = [part for part in full.split("/") if part and part != "."]
    current = "/"
    reason = _network_mount(current, mounts)
    if reason:
        return reason
    for index, part in enumerate(parts):
        if part == "..":
            current = os.path.dirname(current)  # current holds no links: its real parent
            continue
        current = os.path.join(current, part)
        reason = _network_mount(current, mounts)
        if reason:
            return reason
        try:
            mode = lstat(current).st_mode
        except OSError:
            return None  # missing; the existence check rejects it later
        if not stat.S_ISLNK(mode):
            continue
        try:
            link = readlink(current)
        except OSError:
            return "unreadable link"
        if not os.path.isabs(link):
            link = os.path.join(os.path.dirname(current), link)
        remaining = os.path.join(link, *parts[index + 1 :])
        return posix_network_hop(remaining, lstat, readlink, mounts, _depth + 1)
    return None


def _network_mount(path: str, mounts: list[tuple[str, str]]) -> str | None:
    # The innermost mount holding ``path``; later entries stack on earlier ones.
    best, best_type = "", ""
    for mount_point, fs_type in mounts:
        inside = mount_point == "/" or path == mount_point or path.startswith(mount_point + "/")
        if inside and len(mount_point) >= len(best):
            best, best_type = mount_point, fs_type
    if best_type in NETWORK_FS_TYPES or best_type == "fuse" or best_type.startswith("fuse."):
        return f"network or FUSE file system ({best_type}) is not allowed"
    return None


def read_mounts(path: str = MOUNTINFO) -> list[tuple[str, str]]:
    """(mount point, file system type) pairs from /proc/self/mountinfo."""
    with open(path, "rb") as file:
        data = file.read()
    mounts = []
    for line in data.splitlines():
        left, _, right = line.partition(b" - ")
        fields, rest = left.split(), right.split()
        if len(fields) < 5 or not rest:
            continue
        mount_point = re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), fields[4])
        mounts.append((os.fsdecode(mount_point), os.fsdecode(rest[0])))
    return mounts


def posix_looks_executable(path: str) -> bool:
    """True for anything xdg-open might run: exec bit, ELF/PE binaries, scripts, non-regular files."""
    try:
        info = os.stat(path)
    except OSError:
        return True  # cannot tell: reveal only
    if not stat.S_ISREG(info.st_mode):
        return True  # FIFOs and devices: reading could block
    if info.st_mode & 0o111:
        return True
    try:
        with open(path, "rb") as file:
            head = file.read(4)
    except OSError:
        return True
    return head.startswith((b"\x7fELF", b"MZ", b"#!"))
