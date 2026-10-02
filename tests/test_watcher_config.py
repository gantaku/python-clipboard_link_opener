"""Deduper (copyOnSelect burst suppression) and config loading."""

import json
from pathlib import Path

from src.config import DEFAULT_ROOTS, load_config, load_obsidian_vaults
from src.watcher import Deduper

HOME = Path(r"C:\Users\me")


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_first_text_fires():
    assert Deduper(window=2.0, clock=FakeClock()).should_fire("a")


def test_same_text_within_window_is_suppressed():
    clock = FakeClock()
    dedup = Deduper(window=2.0, clock=clock)
    assert dedup.should_fire("a")
    clock.now += 1.0
    assert not dedup.should_fire("a")


def test_same_text_after_window_fires_again():
    clock = FakeClock()
    dedup = Deduper(window=2.0, clock=clock)
    dedup.should_fire("a")
    clock.now += 2.5
    assert dedup.should_fire("a")


def test_different_text_fires_immediately():
    clock = FakeClock()
    dedup = Deduper(window=2.0, clock=clock)
    dedup.should_fire("a")
    assert dedup.should_fire("b")


def test_missing_config_is_created_with_defaults(tmp_path):
    path = tmp_path / "cfg" / "config.json"
    policy = load_config(path, HOME)
    assert path.exists()
    assert policy.open_urls is True
    assert policy.allowed_roots == tuple(HOME / r for r in DEFAULT_ROOTS)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["open_urls"] is True


def test_config_values_are_read(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "open_urls": False,
                "allowed_roots": ["~/x", r"D:\data"],
                "reveal_extensions": [".foo"],
            }
        ),
        encoding="utf-8",
    )
    policy = load_config(path, HOME)
    assert policy.open_urls is False
    assert policy.allowed_roots == (HOME / "x", Path(r"D:\data"))
    assert policy.reveal_extensions == frozenset({".foo"})


def test_broken_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    policy = load_config(path, HOME)
    assert policy.open_urls is True
    assert path.read_text(encoding="utf-8") == "{not json"


def test_wrong_types_fall_back_per_field(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"open_urls": "yes", "allowed_roots": "~/x"}), encoding="utf-8"
    )
    policy = load_config(path, HOME)
    assert policy.open_urls is True
    assert policy.allowed_roots == tuple(HOME / r for r in DEFAULT_ROOTS)


def test_obsidian_vaults_are_read(tmp_path):
    path = tmp_path / "obsidian.json"
    path.write_text(
        json.dumps(
            {
                "vaults": {
                    "a": {"path": r"C:\Users\me\knowledge"},
                    "b": {"path": r"C:\w"},
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_obsidian_vaults(path) == (Path(r"C:\Users\me\knowledge"), Path(r"C:\w"))


def test_obsidian_missing_or_broken_returns_empty(tmp_path):
    assert load_obsidian_vaults(tmp_path / "none.json") == ()
    broken = tmp_path / "broken.json"
    broken.write_text("[", encoding="utf-8")
    assert load_obsidian_vaults(broken) == ()


def test_relative_and_empty_roots_are_skipped(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"allowed_roots": ["foo", "", "~/x"]}), encoding="utf-8")
    assert load_config(path, HOME).allowed_roots == (HOME / "x",)
