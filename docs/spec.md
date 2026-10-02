# Clipboard Link Opener — spec

Design decisions: `~/knowledge/_inbox/2026-10-03-clipboard-link-opener/*-[draft]-adr-*.md`

## Pipeline

```
ClipboardWatcher (poll GetClipboardSequenceNumber every 200ms)
  -> Deduper (same text within 2s fires once)
  -> normalize(text, home) -> Target(path|url, line)      # pure
  -> decide(target, policy) -> Decision(open|folder|reveal|url|reject)
  -> plan_launch(decision, vaults) -> LaunchPlan            # pure
  -> launch(plan)  os.startfile / subprocess.Popen (no shell)
```

## Modules

| Module | Role |
|---|---|
| `src/normalize.py` | Whole-text match only. Joins wrapped lines, strips wrappers/punctuation, parses `:line`, `#Lline`, `file://`, `~`, `/c/`, `/mnt/c/` |
| `src/policy.py` | UNC rejected before any disk access; normpath + case-insensitive root check; must exist; executable types revealed unless a line number is given |
| `src/opener.py` | URL -> shell; folder -> explorer; reveal -> `explorer /select,`; line -> `vscode://file/...:N`; `.md` in an Obsidian vault -> `obsidian://open?path=`; else shell |
| `src/config.py` | `%APPDATA%\clipboard_link_opener\config.json` (created with defaults), Obsidian vaults from `%APPDATA%\obsidian\obsidian.json` |
| `src/watcher.py` | Polling thread; clipboard read with retries; never dies on errors |
| `src/app.py` | Handler (reloads config on mtime change), single-instance mutex, rotating log, pystray tray |

## Safety notes

- Executables are never started from a copy. With a line number they go to VS Code via URI, which only displays them.
- `code.cmd` is not used: batch files re-parse arguments, so a crafted file name could inject commands.
- Content present at startup is never opened.
