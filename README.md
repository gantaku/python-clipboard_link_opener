# Clipboard Link Opener

A tiny Windows tray app that **opens a local file path or URL the moment you copy it** — no pasting into Explorer or the Run box.

Built for AI coding tools (Claude Code, Codex, …) that print file paths as plain text in the terminal. With Windows Terminal's `copyOnSelect`, selecting a path is enough to open it.

[日本語](#日本語)

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
- **Source app filter.** Only copies from terminals and editors count (Windows Terminal, conhost, WezTerm, Alacritty, mintty, Tabby, VS Code, Cursor, Windsurf, Claude). Copying a link in a browser or chat app to paste elsewhere does not open it.
- **Allowed roots.** Paths must exist under an allowed folder (default: your user profile). `..` and junctions/symlinks are resolved before the check.
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
  "reveal_extensions": [".bat", ".exe", "..."]
}
```

| Key | Meaning |
|---|---|
| `open_urls` | `false` to stop opening http(s) links |
| `allowed_roots` | Folders paths must be under (`~` = user profile) |
| `source_apps` | Exe names a copy must come from. `[]` = any app |
| `reveal_extensions` | Types that are only revealed, never opened |

The log (`%APPDATA%\clipboard_link_opener\app.log`) records what was opened or rejected and why.

## Build / test

Requires Windows 10/11 and Python 3.10+.

```cmd
pip install -r requirements.txt
python -m pytest -q
build.bat
```

`build.bat` produces `build\clipboard_link_opener.exe` (PyInstaller). `pythonw run.py` runs it without building.

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
- 設定は `%APPDATA%\clipboard_link_opener\config.json` にあり、URL を開かないようにするなら `open_urls: false` にします。
