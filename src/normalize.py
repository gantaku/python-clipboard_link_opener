"""Turn copied text into an openable target (local path or http(s) URL).

Pure functions only: no disk access here. Existence and allowed-root checks
live in ``policy``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

MAX_LENGTH = 2048

_WRAPPERS = (
    ("`", "`"),
    ('"', '"'),
    ("'", "'"),
    ("「", "」"),
    ("『", "』"),
    ("<", ">"),
    ("(", ")"),
    ("（", "）"),
)
_TRAILING_PUNCT = "。、，．,;:."

_URL_RE = re.compile(r"^https?://[^\s<>\"`]+$", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_POSIX_DRIVE_RE = re.compile(r"^/(?:mnt/)?([A-Za-z])(?:/(.*))?$")
_LINE_RE = re.compile(r"^(?P<path>.+?)(?::(?P<line>\d+)(?::\d+|-\d+)?|#L(?P<gh>\d+))$")


@dataclass(frozen=True)
class Target:
    kind: Literal["path", "url"]
    path: Path | None = None
    line: int | None = None
    url: str | None = None


def normalize(text: str | None, home: Path) -> Target | None:
    """Return a Target when the whole text is a single path or URL, else None."""
    if not text or len(text) > MAX_LENGTH:
        return None
    candidate = _clean(_join_wrapped_lines(text))
    if not candidate:
        return None
    if _URL_RE.match(candidate):
        return Target(kind="url", url=candidate)
    if candidate.lower().startswith("file:"):
        candidate = _file_uri_to_path(candidate)
        if candidate is None:
            return None
    path_text, line = _split_line_suffix(candidate)
    path = _to_windows_path(path_text, home)
    if path is None:
        return None
    return Target(kind="path", path=path, line=line)


_TOKEN_START_RE = re.compile(
    r"^(?:https?://|file:|[A-Za-z]:[\\/]|~|/(?:mnt/)?[A-Za-z]/|\\)", re.IGNORECASE
)


def _join_wrapped_lines(text: str) -> str:
    # Terminals hard-wrap long paths and indent the continuation. A continuation
    # that starts like a new path/URL means several items were copied: refuse.
    parts = [part.strip() for part in text.splitlines() if part.strip()]
    if any(_TOKEN_START_RE.match(part) for part in parts[1:]):
        return ""
    return "".join(parts)


def _clean(text: str) -> str:
    previous = None
    while text != previous:
        previous = text
        text = text.strip().rstrip(_TRAILING_PUNCT)
        for left, right in _WRAPPERS:
            if len(text) >= 2 and text.startswith(left) and text.endswith(right):
                text = text[len(left) : -len(right)]
                break
    return text


def _file_uri_to_path(uri: str) -> str | None:
    parts = urlsplit(uri)
    if parts.scheme.lower() != "file":
        return None
    path = unquote(parts.path)
    if parts.fragment:
        line = re.fullmatch(r"L(\d+)", parts.fragment)
        path += f":{line.group(1)}" if line else "#" + unquote(parts.fragment)
    host = parts.netloc
    if host and host.lower() != "localhost":
        return "\\\\" + host + path.replace("/", "\\")
    # "/C:/Users/..." -> "C:/Users/..."
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def _split_line_suffix(text: str) -> tuple[str, int | None]:
    match = _LINE_RE.match(text)
    if not match:
        return text, None
    path = match.group("path")
    if re.fullmatch(r"[A-Za-z]", path):
        # "C:12" is not "C" + line 12.
        return text, None
    line = match.group("line") or match.group("gh")
    return path, int(line)


def _to_windows_path(text: str, home: Path) -> Path | None:
    if " " in text and not _DRIVE_RE.match(text) and not text.startswith("~"):
        return None
    if text == "~":
        return home
    if text.startswith(("~/", "~\\")):
        return home / text[2:].replace("/", "\\")
    if text.startswith(("\\\\", "//")):
        return Path("\\\\" + text[2:].replace("/", "\\"))
    if _DRIVE_RE.match(text):
        return Path(text.replace("/", "\\"))
    posix = _POSIX_DRIVE_RE.match(text)
    if posix:
        drive, rest = posix.group(1).upper(), posix.group(2) or ""
        return Path(f"{drive}:\\" + rest.replace("/", "\\"))
    return None
