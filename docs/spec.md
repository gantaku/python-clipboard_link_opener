# Clipboard Link Opener — spec

See README.md for behaviour and settings.

## Pipeline

```
ClipboardWatcher (poll GetClipboardSequenceNumber every 200ms)
  -> Deduper (same text within 2s fires once)
  -> normalize(text, home) -> Target(path|url, line)      # pure
  -> source_allowed(owner exe of the clipboard, else foreground window)
  -> decide(target, policy) -> Decision(open|folder|reveal|url|reject)
  -> plan_launch(decision, vaults) -> LaunchPlan            # pure
  -> launch(plan)  os.startfile / subprocess.Popen (no shell)
```

## Modules

| Module | Role |
|---|---|
| `src/normalize.py` | Whole-text match only. Joins wrapped lines, strips wrappers/punctuation, parses `:line`, `#Lline`, `file://`, `~`, `/c/`, `/mnt/c/` |
| `src/policy.py` | UNC and NTFS ADS rejected before any disk access; `network_hop` walks components with lstat/readlink (never following) and rejects network drives and links to shares; realpath + case-insensitive root check; must exist; executable/macro types revealed unless a line number is given; source app filter (unknown owner is not trusted) |
| `src/opener.py` | URL -> shell; folder -> explorer; reveal -> `explorer /select,`; line -> `vscode://file/...:N`; `.md` in an Obsidian vault -> `obsidian://open?path=`; else shell |
| `src/config.py` | `%APPDATA%\clipboard_link_opener\config.json` (created with defaults; strict validation raises ConfigError; `reveal_extensions` is added to the built-in list), Obsidian vaults from `%APPDATA%\obsidian\obsidian.json` |
| `src/watcher.py` | Polling thread; text and owner exe read in one clipboard open, retried when busy or when the sequence number moved during the read; no foreground-window fallback; never dies on errors |
| `src/app.py` | Handler (reloads when config.json or obsidian.json changes, keeps the last good policy on ConfigError, dedups only right before launch), single-instance mutex, rotating log, pystray tray |

## Safety notes

- Executables are never started from a copy. With a line number they go to VS Code via URI, which only displays them.
- `code.cmd` is not used: batch files re-parse arguments, so a crafted file name could inject commands.
- Content present at startup is never opened.
