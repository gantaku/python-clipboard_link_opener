# Clipboard Link Opener

A tiny Windows / Linux (X11) tray app that **opens a local file path or URL the moment you copy it** — no pasting into Explorer or the Run box.

Built for AI coding tools (Claude Code, Codex, …) that print file paths as plain text in the terminal. With Windows Terminal's `copyOnSelect`, or simply selecting text in a Linux terminal, selecting a path is enough to open it.

[日本語](#日本語) · [Linux](#linux)

## How it works

Copy a path (or select it in Windows Terminal) and it opens:

| Copied text | Opens with |
|---|---|
| `https://…` / `http://…` | Default browser |
| A folder | Explorer |
| `app.py:42`, `app.py:42:7`, `app.py#L42` | VS Code at that line (`vscode://` URI) |
| `.md` inside an Obsidian vault | Obsidian |
| Executables / scripts / macro documents (`.exe .bat .ps1 .py .lnk .docm …`) | **Not opened** — Explorer shows the file selected |
| Any other file | Default app |

Accepted notations: `C:\…`, `C:/…`, `~/…`, `/c/…` (Git Bash), `/mnt/c/…` (WSL), `file:///C:/…`.
Surrounding backticks, quotes, brackets and trailing punctuation are stripped, and paths hard-wrapped by the terminal are joined.

## Safety

Opening whatever lands on the clipboard is risky, so it is deliberately narrow:

- **Whole-text match only.** Selecting a sentence that contains a path does nothing.
- **Source app filter.** The copying app is read from the clipboard owner in the same step as the text. Only copies from terminals and editors count (Windows Terminal, conhost, WezTerm, Alacritty, mintty, Tabby, VS Code, Cursor, Windsurf, Claude). Copying a link in a browser or chat app to paste elsewhere does not open it, and a copy whose owner is unknown is ignored.
- **Allowed roots.** Paths must exist under an allowed folder (default: your user profile). `..` and junctions/symlinks are resolved before the check.
- **No network access.** Mapped network drives and links that lead to a share are rejected by reading the links themselves, before anything follows them.
- **Never runs anything.** Executable, script and macro types are only revealed in Explorer. NTFS alternate data streams (`x.exe::$DATA`) and UNC paths (`\\server\share`, which can leak NTLM credentials) are rejected without touching them.
- **No shell.** Explorer is started by absolute path, VS Code through a URI (`code.cmd` would re-parse arguments).
- Content already on the clipboard at startup is ignored, and the same text copied repeatedly within 2 seconds opens once.

## Install

1. Build `build\clipboard_link_opener.exe` with `build.bat` (see below) and run it, or run `pythonw run.py`. A blue icon appears in the tray.
2. To start with Windows: press `Win + R`, enter `shell:startup`, and put a shortcut to the exe there.

Tray menu: Pause / Open settings / Open log / Quit.

## Settings

`%APPDATA%\clipboard_link_opener\config.json` is created on first run. Changes apply from the next copy.

```json
{
  "open_urls": true,
  "allowed_roots": ["~"],
  "source_apps": ["windowsterminal.exe", "code.exe", "..."],
  "reveal_extensions": []
}
```

| Key | Meaning |
|---|---|
| `open_urls` | `false` to stop opening http(s) links |
| `allowed_roots` | Folders paths must be under (`~` = user profile) |
| `source_apps` | Exe names a copy must come from. `[]` = any app |
| `reveal_extensions` | Extra types to only reveal. The built-in list always applies |

An invalid file (bad JSON, wrong type, relative root) never widens the rules: the last valid settings stay in effect, and if there were none yet, nothing opens until the file is fixed. The reason is written to the log.

The log (`%APPDATA%\clipboard_link_opener\app.log`) records what was opened or rejected and why.

## Build / test

Requires Windows 10/11 and Python 3.10+.

```cmd
pip install -r requirements.txt
python -m pytest -q
build.bat
```

Optional tests that use the real clipboard (they overwrite it; only plain text is restored):

```cmd
set CLO_LIVE_CLIPBOARD=1
python -m pytest -q tests/test_watcher_live.py
```

`build.bat` produces `build\clipboard_link_opener.exe` (PyInstaller). `pythonw run.py` runs it without building.

## Linux

Runs on X11 sessions (tested on Ubuntu 22.04 / GNOME). Wayland sessions are refused: the app that made a copy cannot be identified there, so nothing would be safe to open.

What differs from Windows:

| | Linux |
|---|---|
| Notations | `~/…`, `/home/…`, `file:///…` (`/c/…` and `/mnt/c/…` are ordinary paths, `C:\…` is not a path). File names containing `:` are not opened (the Windows stream guard is kept) |
| What is watched | CLIPBOARD (Ctrl+Shift+C) and PRIMARY (just selecting). PRIMARY is read only after the mouse buttons are up and the text has stayed the same for 0.5 s, so a half-finished drag never opens. `watch_primary: false` turns it off |
| Source app | The selection owner's process, found through the X-Resource extension (falling back to `_NET_WM_PID`), by the name of `/proc/<pid>/exe`: `gnome-terminal-server`, `wezterm-gui`, `kitty`, `code`, … Unknown owners are ignored. Terminals written in Python (Terminator, Guake) cannot be told apart from other Python programs and are not in the default list |
| Opens with | `xdg-open` (default app, browser, `vscode://`, `obsidian://`) |
| Reveal only | The Windows list plus `.desktop .AppImage .sh .run .deb .rpm .snap .flatpakref …`, any file with an execute bit, and ELF / PE / `#!` files. They are selected in the file manager (`org.freedesktop.FileManager1.ShowItems`), or their folder is opened |
| Network | Paths are walked component by component against `/proc/self/mountinfo`; NFS, SMB/CIFS, autofs, 9p and every FUSE mount (sshfs, rclone, gvfs, …) are rejected before they are touched, including through symlinks |
| Settings / log | `$XDG_CONFIG_HOME/clipboard_link_opener/` (usually `~/.config/clipboard_link_opener/`) |
| Single instance | `flock` on `$XDG_RUNTIME_DIR/clipboard_link_opener.lock` |
| Tray | pystray (AppIndicator when its GObject bindings are installed, otherwise a plain X11 tray icon). Without any tray the app keeps working in the background |

Install (creates `.venv` in the checkout and an entry in `~/.config/autostart/`):

```sh
scripts/install-linux.sh
scripts/install-linux.sh --remove   # stop starting at login
```

Or run it by hand: `python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python run.py`.

Tests: `python -m pytest -q` runs the shared tests and `tests/test_linux.py`; Windows-only tests are skipped. With `CLO_LIVE_CLIPBOARD=1`, `tests/test_watcher_x11_live.py` uses the real X11 selections (needs `xclip` to put the previous text back).

## Prior art

Clipboard managers such as [CopyQ](https://github.com/hluk/CopyQ) (automatic commands with a window filter), KDE [Klipper](https://userbase.kde.org/Klipper) (regex actions, browsers excluded by default) and [Clipnik](https://github.com/mpeutz/Clipnik) (per-app copy rules, open/reveal actions) can do similar things as part of a larger tool. This project does only this one job, with safe defaults and no setup.

## License

MIT

---

## 日本語

コピーしたローカルファイルのパスや URL を、貼り付けずにそのまま開く Windows 常駐ツールです。Claude Code などの AI コーディングツールがターミナルに平文で出すパスを、Windows Terminal で選択する（`copyOnSelect` で自動的にコピーされる）だけで開けます。

- 開くのは、コピーした文字全体が 1 つのパスか URL のときだけです。
- 対象はターミナルとエディタでコピーしたものだけで、ブラウザやチャットアプリでのコピーは無視します。
- 存在するパスで、許可したフォルダの下にあるものだけを開きます。
- 実行ファイル・スクリプト・マクロ付き文書は開かず、Explorer で選択して見せるだけです。
- ネットワークドライブや、共有フォルダにつながるリンクは開きません（リンクをたどる前に判定します）。
- 設定ファイルが壊れていても許可範囲は広がりません（直前の正しい設定を使い続けます）。
- 設定は `%APPDATA%\clipboard_link_opener\config.json` にあり、URL を開かないようにするなら `open_urls: false` にします。

### Linux

- X11 のセッションで動きます（Ubuntu 22.04 / GNOME で確認）。Wayland では、どのアプリがコピーしたか分からないので、何も開かずに止まります。
- ターミナルで選択するだけ（PRIMARY）でも開きます。マウスのボタンが離れ、同じ文字が 0.5 秒続いてから読むので、ドラッグの途中で開くことはありません。止めるなら `watch_primary: false` にします。
- コピー元は、クリップボードを持つプロセスの実行ファイル名（`gnome-terminal-server`、`wezterm-gui`、`code` など）で判定します。
- 実行権限の付いたファイル、`.desktop`・`.sh`・AppImage などは開かず、ファイルマネージャで選択して見せるだけです。
- NFS・SMB・sshfs などのネットワーク上の場所や FUSE のマウントは、シンボリックリンクの先にあっても、触る前に拒否します。
- 設定とログは `~/.config/clipboard_link_opener/` にあります。`scripts/install-linux.sh` を実行すると、ログイン時に自動で起動するよう登録されます。
