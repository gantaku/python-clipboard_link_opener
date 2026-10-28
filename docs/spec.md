# Clipboard Link Opener — spec

See README.md for behaviour and settings.

## Pipeline

```
ClipboardWatcher (poll every 200ms: GetClipboardSequenceNumber / X11 XFixes owner-change count)
  -> Deduper (same text within 2s fires once)
  -> normalize(text, home) -> Target(path|url, line)      # pure
  -> source_allowed(owner exe of the clipboard; never the foreground window)
  -> decide(target, policy) -> Decision(open|folder|reveal|url|reject)
  -> plan_launch(decision, vaults) -> LaunchPlan            # pure
  -> launch(plan)  os.startfile / xdg-open / subprocess.Popen (no shell)
```

The rules are shared. Only the OS layer differs, chosen by `src/osinfo.IS_WINDOWS`:
path notation (`normalize`), network check and executable check (`policy`),
launch (`opener`), settings location and default source apps (`config`),
clipboard reading (`watcher` / `watcher_x11`) and single instance (`app`).

## Modules

| Module | Role |
|---|---|
| `src/osinfo.py` | `IS_WINDOWS` |
| `src/normalize.py` | Whole-text match only. Joins wrapped lines, strips wrappers/punctuation, parses `:line`, `#Lline`, `file://`, `~`. Windows: `C:\`, `/c/`, `/mnt/c/`. Linux: absolute POSIX paths; a continuation line starting with `/` is a second item (except after a URL) |
| `src/policy.py` | UNC and NTFS ADS rejected before any disk access; network check walks components without following links (Windows `network_hop`: drive type + reparse points; Linux `posix_network_hop`: the innermost mount from `/proc/self/mountinfo`, checked before each lstat, network types and all FUSE rejected, unreadable table rejects); realpath + root check (case-insensitive on Windows only); must exist; executable/macro types revealed unless a line number is given (Linux also: exec bit, ELF/PE/`#!` header, non-regular files); source app filter (unknown owner is not trusted) |
| `src/opener.py` | URL -> shell; folder -> explorer / xdg-open; reveal -> `explorer /select,` / D-Bus `FileManager1.ShowItems`, else the parent folder; line -> `vscode://file/...:N`; `.md` in an Obsidian vault -> `obsidian://open?path=`; else shell. Linux commands only from `/usr/local/bin:/usr/bin:/bin` |
| `src/config.py` | `%APPDATA%\clipboard_link_opener\config.json` or `$XDG_CONFIG_HOME/clipboard_link_opener/config.json` (created with defaults; strict validation raises ConfigError; `reveal_extensions` is added to the built-in list; Linux adds `watch_primary`), Obsidian vaults from `obsidian.json` (Linux: native, else Flatpak) |
| `src/watcher.py` | Polling thread over a `sequence` / `reader` pair; Windows reader: text and owner exe read in one clipboard open, retried when busy or when the sequence number moved during the read; never dies on errors |
| `src/watcher_x11.py` | Linux reader: XFixes `SetSelectionOwnerNotify` on CLIPBOARD and PRIMARY bumps the count; text via `UTF8_STRING` conversion (INCR refused); owner PID through X-Resource, else `_NET_WM_PID`, then `/proc/<pid>/exe`; owner changed during the read -> busy. PRIMARY waits for the mouse buttons to be up and the text to stay the same for 0.5 s. Refuses Wayland sessions |
| `src/app.py` | Handler (reloads when config.json or obsidian.json changes, keeps the last good policy on ConfigError, dedups only right before launch), single-instance mutex / flock, rotating log, pystray tray (Linux: X11 icon fallback, or no tray) |

## Safety notes

- Executables are never started from a copy. With a line number they go to VS Code via URI, which only displays them.
- `code.cmd` is not used: batch files re-parse arguments, so a crafted file name could inject commands.
- Content present at startup is never opened.
- Linux: the D-Bus call gets a percent-encoded `file://` URI, so a file name cannot break out of the GVariant string. A local mount that is reached through a network mount is rejected too, since looking it up crosses the network directory.
