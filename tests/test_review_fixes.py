"""Regression tests for the Codex review (2026-10-03)."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.app as app
from src.normalize import normalize
from src.policy import DRIVE_FIXED, DRIVE_REMOTE, network_hop
from src.watcher import ClipboardBusy, ClipboardWatcher, Deduper

HOME = Path(r"C:\Users\me")
REPARSE = 0x400


# --- #1 network links are rejected before anything follows them -------------


def fake_fs(links: dict[str, str], remote_drives=()):
    def lstat(path):
        attrs = REPARSE if path.lower() in {k.lower() for k in links} else 0
        return SimpleNamespace(st_file_attributes=attrs)

    def readlink(path):
        for key, value in links.items():
            if key.lower() == path.lower():
                return value
        raise OSError("not a link")

    def drive_type(root):
        return DRIVE_REMOTE if root[0].upper() in remote_drives else DRIVE_FIXED

    return {"lstat": lstat, "readlink": readlink, "drive_type": drive_type}


def test_plain_local_path_has_no_network_hop():
    assert network_hop(r"C:\Users\me\a.md", **fake_fs({})) is None


def test_symlink_to_unc_is_detected_without_following():
    fs = fake_fs({r"C:\Users\me\share": r"\\?\UNC\server\share"})
    assert network_hop(r"C:\Users\me\share\a.md", **fs) is not None


def test_junction_to_mapped_network_drive_is_detected():
    fs = fake_fs({r"C:\Users\me\z": r"\??\Z:\data"}, remote_drives="Z")
    assert network_hop(r"C:\Users\me\z\a.md", **fs) is not None


def test_mapped_network_drive_is_detected():
    assert network_hop(r"Z:\a.md", **fake_fs({}, remote_drives="Z")) is not None


def test_chained_local_links_are_followed():
    fs = fake_fs({r"C:\Users\me\a": r"C:\other", r"C:\other": r"\\server\x"})
    assert network_hop(r"C:\Users\me\a\f.md", **fs) is not None


def test_relative_local_link_is_fine():
    fs = fake_fs({r"C:\Users\me\a": r"..\b"})
    assert network_hop(r"C:\Users\me\a\f.md", **fs) is None


def test_reparse_point_that_is_not_a_link_is_ignored():
    # OneDrive placeholders are reparse points that readlink() refuses.
    def lstat(path):
        return SimpleNamespace(st_file_attributes=REPARSE)

    def readlink(path):
        raise OSError("not a symbolic link")

    hop = network_hop(
        r"C:\Users\me\a.md",
        lstat=lstat,
        readlink=readlink,
        drive_type=lambda r: DRIVE_FIXED,
    )
    assert hop is None


def test_link_loop_is_rejected():
    fs = fake_fs({r"C:\a": r"C:\b", r"C:\b": r"C:\a"})
    assert network_hop(r"C:\a\x.md", **fs) is not None


# --- handler fixtures --------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    (home / "work" / "a.txt").write_text("x", encoding="utf-8")
    (home / "work" / "run.bat").write_text("x", encoding="utf-8")
    config = tmp_path / "config.json"
    obsidian = tmp_path / "obsidian.json"
    monkeypatch.setattr(app, "obsidian_config_path", lambda: obsidian)
    launched = []
    monkeypatch.setattr(app, "launch", launched.append)
    return home, config, obsidian, launched


def write(path: Path, data, bump: int = 0) -> None:
    path.write_text(
        json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8"
    )
    if bump:
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))


# --- #2 extension list can only grow -----------------------------------------


def test_custom_reveal_list_keeps_builtin_protection(env):
    home, config, _, launched = env
    write(
        config,
        {"allowed_roots": ["~/work"], "source_apps": [], "reveal_extensions": [".foo"]},
    )
    app.Handler(config, home)("~/work/run.bat")
    assert launched[0].arg[1] == "/select,"


# --- #3 a broken config never widens the policy ------------------------------


def test_broken_reload_keeps_last_good_policy(env):
    home, config, _, launched = env
    write(config, {"allowed_roots": ["~/work"], "source_apps": [], "open_urls": False})
    handler = app.Handler(config, home)
    write(config, "{half written", bump=5)
    handler("https://example.com")
    assert launched == []


def test_broken_config_at_start_opens_nothing_until_fixed(env):
    home, config, _, launched = env
    write(config, "{half written")
    handler = app.Handler(config, home)
    handler("~/work/a.txt")
    assert launched == []
    write(config, {"allowed_roots": ["~/work"], "source_apps": []}, bump=5)
    handler("~/work/a.txt")
    assert len(launched) == 1


# --- #5 / #10 reload detection -------------------------------------------------


def test_change_during_load_is_reloaded_next_time(env, monkeypatch):
    home, config, _, launched = env
    write(config, {"allowed_roots": ["~/work"], "source_apps": []})
    real_load = app.load_config
    calls = []

    def racing_load(path, h):
        policy = real_load(path, h)
        if not calls:
            # Saved again while the first load was running.
            write(
                config,
                {"allowed_roots": ["~/work"], "source_apps": [], "open_urls": False},
                bump=5,
            )
        calls.append(1)
        return policy

    monkeypatch.setattr(app, "load_config", racing_load)
    handler = app.Handler(config, home)
    handler("https://example.com")
    assert launched == []
    assert len(calls) == 2


def test_new_obsidian_vault_is_picked_up(env):
    home, config, obsidian, launched = env
    (home / "work" / "n.md").write_text("x", encoding="utf-8")
    write(config, {"allowed_roots": ["~/work"], "source_apps": []})
    handler = app.Handler(config, home)
    handler("~/work/n.md")
    assert not launched[-1].arg.startswith("obsidian://")
    write(obsidian, {"vaults": {"v": {"path": str(home / "work")}}}, bump=5)
    handler._dedup = Deduper(window=0)  # same path again right away
    handler("~/work/n.md")
    assert launched[-1].arg.startswith("obsidian://")


# --- #6 dedup only counts real launches --------------------------------------


def test_ignored_copy_does_not_suppress_the_next_real_one(env):
    home, config, _, launched = env
    write(config, {"allowed_roots": ["~/work"], "source_apps": ["windowsterminal.exe"]})
    handler = app.Handler(config, home)
    handler("https://example.com", "chrome.exe")
    handler("https://example.com", "windowsterminal.exe")
    assert len(launched) == 1


def test_rejected_copy_does_not_suppress_the_next_one(env):
    home, config, _, launched = env
    write(config, {"allowed_roots": ["~/work"], "source_apps": []})
    handler = app.Handler(config, home)
    handler("~/work/later.txt")
    (home / "work" / "later.txt").write_text("x", encoding="utf-8")
    handler("~/work/later.txt")
    assert len(launched) == 1


# --- #7 / #8 / #9 normalization ------------------------------------------------


def test_encoded_hash_in_file_uri_is_part_of_the_name():
    target = normalize("file:///C:/Users/me/a%23L10", HOME)
    assert target.path == Path(r"C:\Users\me\a#L10")
    assert target.line is None


def test_file_uri_fragment_still_gives_line():
    target = normalize("file:///C:/Users/me/a.md#L7", HOME)
    assert (target.path, target.line) == (Path(r"C:\Users\me\a.md"), 7)


@pytest.mark.parametrize(
    "text",
    [
        "/mnt/c/Users/me/my project/a.md",
        "/c/Users/me/my project/a.md",
        '"/c/Users/me/my project/a.md"',
    ],
)
def test_posix_paths_with_spaces(text):
    assert normalize(text, HOME).path == Path(r"C:\Users\me\my project\a.md")


@pytest.mark.parametrize(
    "text",
    ['"C:\\Users\\me\\a.md":42', "`C:\\Users\\me\\a.md`:42", "'~/a.md':42"],
)
def test_quoted_path_with_line_outside(text):
    target = normalize(text, HOME)
    assert target.path.name == "a.md"
    assert target.line == 42


# --- #4 / #11 / #12 watcher with fakes ----------------------------------------


class FakeClipboard:
    def __init__(self):
        self.seq = 1
        self.items = []  # what read() returns next: (text, source) or an exception

    def sequence(self):
        return self.seq

    def read(self):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run_watcher(fake, steps):
    received = []
    done = threading.Event()

    def on_text(text, source):
        received.append((text, source))

    watcher = ClipboardWatcher(
        on_text, poll=0.01, sequence=fake.sequence, reader=fake.read
    )
    watcher.start()
    try:
        for step in steps:
            step()
            done.wait(0.08)
    finally:
        watcher.stop()
        watcher.join(1.0)
    return received


def test_watcher_delivers_text_with_its_source():
    fake = FakeClipboard()

    def copy():
        fake.items.append(("C:\\a.md", "windowsterminal.exe"))
        fake.seq += 1

    assert run_watcher(fake, [copy]) == [("C:\\a.md", "windowsterminal.exe")]


def test_watcher_rereads_when_busy():
    fake = FakeClipboard()

    def copy():
        fake.items.extend([ClipboardBusy(), ("x", "code.exe")])
        fake.seq += 1

    assert run_watcher(fake, [copy]) == [("x", "code.exe")]


def test_watcher_survives_sequence_and_read_errors():
    fake = FakeClipboard()
    calls = {"n": 0}
    real_sequence = fake.sequence

    def flaky_sequence():
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("boom")
        return real_sequence()

    fake.sequence = flaky_sequence

    def bad_copy():
        fake.items.append(RuntimeError("read failed"))
        fake.seq += 1

    def good_copy():
        fake.items.append(("y", None))
        fake.seq += 1

    assert run_watcher(fake, [bad_copy, good_copy]) == [("y", None)]


def test_watcher_survives_handler_errors():
    fake = FakeClipboard()
    received = []

    def on_text(text, source):
        received.append(text)
        if len(received) == 1:
            raise RuntimeError("handler boom")

    watcher = ClipboardWatcher(
        on_text, poll=0.01, sequence=fake.sequence, reader=fake.read
    )
    watcher.start()
    try:
        for text in ("one", "two"):
            fake.items.append((text, None))
            fake.seq += 1
            threading.Event().wait(0.08)
    finally:
        watcher.stop()
        watcher.join(1.0)
    assert received == ["one", "two"]


def test_unknown_owner_is_not_trusted():
    from src.policy import Policy, source_allowed

    policy = Policy(allowed_roots=(), source_apps=frozenset({"windowsterminal.exe"}))
    assert not source_allowed(None, policy)
