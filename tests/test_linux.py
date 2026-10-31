"""Linux layer: POSIX paths, mount-based network check, xdg-open / D-Bus launch, XDG config."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.app as app
import src.opener as opener
from src.config import ConfigError, LINUX_SOURCE_APPS, app_dir, load_config, obsidian_config_path
from src.normalize import Target, normalize
from src.opener import LaunchPlan, plan_launch
from src.policy import Decision, Policy, decide, posix_looks_executable, posix_network_hop, read_mounts
from src.watcher_x11 import exe_name, session_problem

from tests.conftest import linux_only

pytestmark = linux_only

HOME = Path("/home/me")
LOCAL = [("/", "ext4")]


# --- normalize -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("~/knowledge/a.md", "/home/me/knowledge/a.md"),
        ("~", "/home/me"),
        ("/home/me/my project/a.md", "/home/me/my project/a.md"),
        ("/c/Users/me/a.md", "/c/Users/me/a.md"),  # not a Windows drive here
        ("/mnt/c/Users/me/a.md", "/mnt/c/Users/me/a.md"),
        ("file:///home/me/a%20b.md", "/home/me/a b.md"),
        ("`~/knowledge/a.md`。", "/home/me/knowledge/a.md"),
        ("「/home/me/a.md」", "/home/me/a.md"),
        ("/home/me/a\\b.md", "/home/me/a\\b.md"),  # a backslash is part of the name
    ],
)
def test_posix_notations(text, expected):
    target = normalize(text, HOME)
    assert target is not None and target.kind == "path"
    assert target.path == Path(expected)


@pytest.mark.parametrize("text", ["C:\\Users\\me\\a.md", "C:/Users/me/a.md", "~\\a.md", "relative/a.md"])
def test_windows_and_relative_notations_are_not_paths(text):
    assert normalize(text, HOME) is None


@pytest.mark.parametrize(
    "text, line",
    [("~/src/app.py:42", 42), ("/home/me/src/app.py:42:7", 42), ("~/src/app.py#L9", 9), ("file:///home/me/a.py#L3", 3)],
)
def test_posix_line_suffix(text, line):
    assert normalize(text, HOME).line == line


def test_network_style_paths_are_kept_for_policy_to_reject():
    for text in ("//server/share/a.md", "file://server/share/a.md"):
        target = normalize(text, HOME)
        assert str(target.path).startswith(("//", "\\\\"))
        assert decide(target, Policy(allowed_roots=(Path("/"),))).action == "reject"


def test_wrapped_path_is_joined():
    target = normalize("/home/me/knowledge/_inbox/2026-10-28-x/2026-[re\n  port]-slug.md", HOME)
    assert target.path == Path("/home/me/knowledge/_inbox/2026-10-28-x/2026-[report]-slug.md")


def test_continuation_starting_with_slash_is_refused():
    # "/home/me/a.md\n/home/me/b.md" is two paths, not one.
    assert normalize("/home/me/a.md\n/home/me/b.md", HOME) is None
    assert normalize("~/a\n/b.md", HOME) is None


def test_wrapped_url_may_continue_with_slash():
    assert normalize("https://example.com/very/long\n  /path?q=1", HOME).url == (
        "https://example.com/very/long/path?q=1"
    )


# --- policy: files on disk -------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "note.md").write_text("x", encoding="utf-8")
    (allowed / "sub").mkdir()
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    return allowed


def local_decide(path, root, line=None, **kwargs):
    kwargs.setdefault("network_check", lambda p: posix_network_hop(p, mounts=LOCAL))
    return decide(Target(kind="path", path=path, line=line), Policy(allowed_roots=(root,)), **kwargs)


def test_plain_file_opens(root):
    assert local_decide(root / "note.md", root).action == "open"


def test_root_match_is_case_sensitive(root):
    upper = Path(str(root).upper()) / "NOTE.MD"
    assert local_decide(upper, root).action == "reject"


def test_symlink_escaping_root_is_rejected(root, tmp_path):
    (root / "jump").symlink_to(tmp_path)
    assert local_decide(root / "jump" / "outside.md", root).action == "reject"


def test_symlink_inside_root_is_followed(root):
    (root / "alias.md").symlink_to(root / "note.md")
    decision = local_decide(root / "alias.md", root)
    assert decision.action == "open"
    assert decision.target.path == root / "note.md"


def test_dotdot_through_link_is_resolved_by_the_link(root, tmp_path):
    deep = tmp_path / "deep" / "er"
    deep.mkdir(parents=True)
    (tmp_path / "deep" / "x.md").write_text("x", encoding="utf-8")
    (root / "jump").symlink_to(deep)
    # Lexically this is root/x.md; through the link it is tmp/deep/x.md (outside).
    assert local_decide(root / "jump" / ".." / "x.md", root).action == "reject"


@pytest.mark.parametrize(
    "name", ["run.sh", "app.desktop", "Tool.AppImage", "setup.run", "pkg.deb", "x.exe", "m.docm"]
)
def test_linux_launchers_and_windows_types_are_revealed(root, name):
    (root / name).write_text("x", encoding="utf-8")
    assert local_decide(root / name, root).action == "reveal"


def test_exec_bit_is_revealed(root):
    tool = root / "tool"
    tool.write_text("x", encoding="utf-8")
    tool.chmod(0o755)
    assert local_decide(tool, root).action == "reveal"


@pytest.mark.parametrize("head", [b"\x7fELF....", b"MZ\x90\x00", b"#!/bin/sh\necho hi\n"])
def test_binaries_and_scripts_without_exec_bit_are_revealed(root, head):
    blob = root / "blob.txt"
    blob.write_bytes(head)
    assert local_decide(blob, root).action == "reveal"


def test_fifo_is_revealed_without_reading_it(root):
    fifo = root / "pipe"
    os.mkfifo(fifo)
    assert posix_looks_executable(str(fifo))
    assert local_decide(fifo, root).action == "reveal"


def test_executable_with_line_goes_to_editor(root):
    (root / "run.sh").write_text("x", encoding="utf-8")
    assert local_decide(root / "run.sh", root, line=3).action == "open"


def test_default_checks_use_the_real_mount_table(root):
    # tmp_path is on a local file system; the default network check reads /proc/self/mountinfo.
    decision = decide(Target(kind="path", path=root / "note.md"), Policy(allowed_roots=(root,)))
    assert decision.action == "open"


# --- policy: network mounts -----------------------------------------------------


class RecordingFs:
    """Fake lstat/readlink that records every path touched."""

    def __init__(self, links=None):
        self.links = links or {}
        self.touched = []

    def lstat(self, path):
        self.touched.append(path)
        mode = 0o120777 if path in self.links else 0o040755
        return SimpleNamespace(st_mode=mode)

    def readlink(self, path):
        return self.links[path]


@pytest.mark.parametrize("fs_type", ["nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "fuse.rclone", "fuse", "autofs"])
def test_network_mount_is_rejected_before_touching_it(fs_type):
    fs = RecordingFs()
    mounts = LOCAL + [("/home/me/net", fs_type)]
    reason = posix_network_hop("/home/me/net/a.md", fs.lstat, fs.readlink, mounts)
    assert reason is not None and fs_type in reason
    assert "/home/me/net" not in fs.touched


def test_link_to_network_mount_is_rejected_without_following():
    fs = RecordingFs({"/home/me/share": "/mnt/nas/data"})
    mounts = LOCAL + [("/mnt/nas", "cifs")]
    assert posix_network_hop("/home/me/share/a.md", fs.lstat, fs.readlink, mounts) is not None
    assert not any(p.startswith("/mnt/nas") for p in fs.touched)


def test_relative_link_and_local_mounts_are_fine():
    fs = RecordingFs({"/home/me/a": "../me/b"})
    mounts = LOCAL + [("/home", "ext4"), ("/home/me/usb", "fuseblk"), ("/boot", "vfat")]
    assert posix_network_hop("/home/me/a/f.md", fs.lstat, fs.readlink, mounts) is None


def test_local_mount_reached_through_a_network_mount_is_rejected():
    # Looking up /srv/cache goes through the NFS directory /srv first.
    mounts = LOCAL + [("/srv", "nfs"), ("/srv/cache", "ext4")]
    fs = RecordingFs()
    assert posix_network_hop("/srv/cache/a", fs.lstat, fs.readlink, mounts) is not None
    assert fs.touched == []


def test_link_loop_is_rejected():
    fs = RecordingFs({"/a": "/b", "/b": "/a"})
    assert posix_network_hop("/a/x", fs.lstat, fs.readlink, LOCAL) is not None


def test_unreadable_mount_table_fails_closed(monkeypatch):
    def broken():
        raise OSError("no /proc")

    monkeypatch.setattr("src.policy.read_mounts", broken)
    assert posix_network_hop("/home/me/a.md") is not None


def test_mountinfo_is_parsed_with_escapes(tmp_path):
    info = tmp_path / "mountinfo"
    info.write_bytes(
        b"22 1 253:0 / / rw,relatime shared:1 - ext4 /dev/root rw\n"
        b"90 22 0:50 / /home/me/my\\040share rw shared:40 - cifs //nas/s rw\n"
        b"91 22 0:51 / /home/me/\xe6\x97\xa5 rw - fuse.sshfs me@h: rw\n"
    )
    assert read_mounts(str(info)) == [
        ("/", "ext4"),
        ("/home/me/my share", "cifs"),
        ("/home/me/日", "fuse.sshfs"),
    ]


# --- opener ------------------------------------------------------------------------

VAULTS = (Path("/home/me/knowledge"),)


def plan(action, path=None, line=None, url=None):
    kind = "url" if url else "path"
    return plan_launch(Decision(action, Target(kind=kind, path=path, line=line, url=url)), VAULTS)


def test_folder_and_file_go_to_xdg_open():
    assert plan("folder", Path("/home/me/knowledge")) == LaunchPlan("shell", "/home/me/knowledge")
    assert plan("open", Path("/home/me/a.pdf")) == LaunchPlan("shell", "/home/me/a.pdf")


def test_reveal_uses_the_file_manager():
    assert plan("reveal", Path("/home/me/run.sh")) == LaunchPlan("reveal", "/home/me/run.sh")


def test_line_uses_vscode_uri():
    assert plan("open", Path("/home/me/my app.py"), line=42) == LaunchPlan(
        "shell", "vscode://file/home/me/my%20app.py:42"
    )
    assert plan("open", Path("/home/me/a\\b.py"), line=1).arg == "vscode://file/home/me/a%5Cb.py:1"


def test_markdown_in_vault_opens_obsidian_case_sensitively():
    assert plan("open", Path("/home/me/knowledge/a b.md")).arg.startswith("obsidian://open?path=%2Fhome")
    assert plan("open", Path("/home/me/Knowledge/a.md")).arg == "/home/me/Knowledge/a.md"


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(opener, "_system_command", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opener.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    return calls


def test_launch_runs_xdg_open_without_a_shell(spawned):
    opener.launch(LaunchPlan("shell", "https://example.com/a?b=1&c=2"))
    args, kwargs = spawned[0]
    assert args == ["/usr/bin/xdg-open", "https://example.com/a?b=1&c=2"]
    assert not kwargs.get("shell")


def test_reveal_calls_show_items_with_an_encoded_uri(spawned, monkeypatch):
    runs = []

    def run(args, **kwargs):
        runs.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(opener.subprocess, "run", run)
    opener.launch(LaunchPlan("reveal", "/home/me/it's a #1.sh"))
    assert runs[0][0] == "/usr/bin/gdbus"
    assert "org.freedesktop.FileManager1.ShowItems" in runs[0]
    assert "['file:///home/me/it%27s%20a%20%231.sh']" in runs[0]
    assert spawned == []


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_reveal_falls_back_to_the_parent_folder(spawned, monkeypatch, failure):
    def run(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 5)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(opener.subprocess, "run", run)
    opener.launch(LaunchPlan("reveal", "/home/me/w/run.sh"))
    assert spawned[0][0] == ["/usr/bin/xdg-open", "/home/me/w"]


def test_commands_are_looked_up_only_in_system_directories(monkeypatch):
    seen = {}

    def which(name, path=None):
        seen[name] = path
        return None

    monkeypatch.setattr(opener.shutil, "which", which)
    with pytest.raises(OSError):
        opener._system_command("xdg-open")
    assert seen["xdg-open"] == "/usr/local/bin:/usr/bin:/bin"


# --- config ------------------------------------------------------------------------


def test_app_dir_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert app_dir() == tmp_path / "clipboard_link_opener"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")
    assert app_dir() == Path.home() / ".config" / "clipboard_link_opener"


def test_obsidian_flatpak_config_is_used_when_native_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert obsidian_config_path() == tmp_path / "cfg" / "obsidian" / "obsidian.json"
    flatpak = tmp_path / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json"
    flatpak.parent.mkdir(parents=True)
    flatpak.write_text("{}", encoding="utf-8")
    assert obsidian_config_path() == flatpak


def test_default_config_has_linux_apps_and_primary(tmp_path):
    path = tmp_path / "config.json"
    policy = load_config(path, HOME)
    assert policy.allowed_roots == (HOME,)
    assert policy.source_apps == frozenset(LINUX_SOURCE_APPS)
    assert "gnome-terminal-server" in policy.source_apps
    assert policy.watch_primary is True
    assert {".desktop", ".appimage", ".bat"} <= policy.reveal_extensions
    assert json.loads(path.read_text(encoding="utf-8"))["watch_primary"] is True


@pytest.mark.parametrize("data", [{"watch_primary": "yes"}, {"allowed_roots": ["C:\\data"]}])
def test_invalid_linux_values_raise(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path, HOME)


# --- app -------------------------------------------------------------------------------


def test_lock_file_allows_one_instance(tmp_path):
    first = app.acquire_lock_file(tmp_path / "x.lock")
    assert first is not None
    assert app.acquire_lock_file(tmp_path / "x.lock") is None
    first.close()
    again = app.acquire_lock_file(tmp_path / "x.lock")
    assert again is not None
    again.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    (home / "work" / "a.txt").write_text("x", encoding="utf-8")
    (home / "work" / "run.sh").write_text("x", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"allowed_roots": ["~/work"], "source_apps": ["gnome-terminal-server"]}))
    monkeypatch.setattr(app, "obsidian_config_path", lambda: tmp_path / "none.json")
    launched = []
    monkeypatch.setattr(app, "launch", launched.append)
    return home, config, launched


def test_handler_opens_terminal_copies_and_reveals_scripts(env):
    home, config, launched = env
    handler = app.Handler(config, home)
    handler("~/work/a.txt", "gnome-terminal-server")
    handler("~/work/run.sh", "gnome-terminal-server")
    handler(str(home / "work" / "a.txt"), "chrome")
    assert launched == [
        LaunchPlan("shell", str(home / "work" / "a.txt")),
        LaunchPlan("reveal", str(home / "work" / "run.sh")),
    ]


def test_watch_primary_follows_the_settings(env):
    home, config, _ = env
    handler = app.Handler(config, home)
    assert handler.watch_primary() is True  # default on Linux
    config.write_text(json.dumps({"allowed_roots": ["~/work"], "watch_primary": False}))
    stat = config.stat()
    os.utime(config, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert handler.watch_primary() is False


def test_watch_primary_is_off_without_valid_settings(env):
    home, config, _ = env
    config.write_text("{broken")
    assert app.Handler(config, home).watch_primary() is False


# --- watcher helpers ------------------------------------------------------------------


def test_exe_name_from_proc():
    links = {"/proc/7/exe": "/usr/libexec/gnome-terminal-server", "/proc/8/exe": "/usr/share/code/code (deleted)"}

    def readlink(path):
        if path not in links:
            raise PermissionError(path)
        return links[path]

    assert exe_name(7, readlink) == "gnome-terminal-server"
    assert exe_name(8, readlink) == "code"
    assert exe_name(9, readlink) is None


@pytest.mark.parametrize(
    "environ, ok",
    [
        ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, True),
        ({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}, False),
        ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, False),
        ({"XDG_SESSION_TYPE": "x11"}, False),
    ],
)
def test_session_problem(environ, ok):
    assert (session_problem(environ) is None) is ok


# --- tray re-docking ------------------------------------------------------------------


def test_tray_watch_fires_when_a_new_host_appears(monkeypatch):
    from src.watcher_x11 import TrayHostWatch

    monkeypatch.setattr(TrayHostWatch, "POLL_SECONDS", 0.01)
    owners = iter([10, 10, 0, 0, 20, 20])
    fired = []
    watch = TrayHostWatch(on_change=lambda: fired.append(1), owner=lambda: next(owners, 20))
    watch.start()
    watch.join(2.0)
    assert fired == [1]
    assert not watch.is_alive()


def test_tray_watch_ignores_the_same_host_and_a_gap(monkeypatch):
    from src.watcher_x11 import TrayHostWatch

    monkeypatch.setattr(TrayHostWatch, "POLL_SECONDS", 0.01)
    fired = []
    watch = TrayHostWatch(on_change=lambda: fired.append(1), owner=iter([10, 0, 10, 10] + [10] * 100).__next__)
    watch.start()
    threading_wait(0.2)
    watch.stop()
    watch.join(1.0)
    assert fired == []


def threading_wait(seconds):
    import threading

    threading.Event().wait(seconds)


def test_redocking_recreates_the_icon_until_quit():
    import threading

    quitting = threading.Event()
    runs = []

    class FakeIcon:
        def __init__(self, n):
            self.n = n

        def run(self):
            runs.append(self.n)
            if self.n == 2:
                quitting.set()  # the user chose 終了 on the second icon

        def stop(self):
            pass

    class FakeWatch:
        def __init__(self, on_change):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    counter = iter(range(1, 10))
    app.run_redocking(lambda: FakeIcon(next(counter)), quitting, watch_factory=FakeWatch)
    assert runs == [1, 2]
