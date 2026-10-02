"""Load user settings and the Obsidian vault list."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.policy import DEFAULT_REVEAL_EXTENSIONS, Policy

log = logging.getLogger(__name__)

APP_DIR_NAME = "clipboard_link_opener"
DEFAULT_ROOTS = ("knowledge", "workspaces", ".claude", "Downloads")


def app_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / APP_DIR_NAME


def obsidian_config_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "obsidian" / "obsidian.json"


def default_settings() -> dict:
    return {
        "open_urls": True,
        "allowed_roots": [f"~/{r}" for r in DEFAULT_ROOTS],
        "reveal_extensions": sorted(DEFAULT_REVEAL_EXTENSIONS),
    }


def load_config(path: Path, home: Path) -> Policy:
    data = _read_or_create(path)
    defaults = default_settings()

    open_urls = data.get("open_urls", defaults["open_urls"])
    if not isinstance(open_urls, bool):
        log.warning("config: open_urls must be true/false; using default")
        open_urls = defaults["open_urls"]

    roots = data.get("allowed_roots", defaults["allowed_roots"])
    if not _is_str_list(roots):
        log.warning("config: allowed_roots must be a list of strings; using default")
        roots = defaults["allowed_roots"]

    exts = data.get("reveal_extensions", defaults["reveal_extensions"])
    if not _is_str_list(exts):
        log.warning(
            "config: reveal_extensions must be a list of strings; using default"
        )
        exts = defaults["reveal_extensions"]

    expanded = tuple(_expand(r, home) for r in roots if r.strip())
    absolute = tuple(r for r in expanded if r.is_absolute())
    if len(absolute) != len(roots):
        log.warning("config: relative or empty allowed_roots entries were skipped")
    return Policy(
        allowed_roots=absolute,
        open_urls=open_urls,
        reveal_extensions=frozenset(e.lower() for e in exts),
    )


def load_obsidian_vaults(path: Path) -> tuple[Path, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            Path(v["path"]) for v in data.get("vaults", {}).values() if "path" in v
        )
    except (OSError, ValueError, AttributeError, TypeError) as error:
        log.info("obsidian vaults unavailable: %s", error)
        return ()


def _read_or_create(path: Path) -> dict:
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(default_settings(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as error:
            log.warning("config: cannot create %s: %s", path, error)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("config: cannot read %s (%s); using defaults", path, error)
        return {}
    return data if isinstance(data, dict) else {}


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _expand(root: str, home: Path) -> Path:
    if root == "~":
        return home
    if root.startswith(("~/", "~\\")):
        return home / root[2:]
    return Path(root)
