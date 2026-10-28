#!/bin/sh
# Set up a virtualenv in this checkout and start Clipboard Link Opener at login.
# Usage: scripts/install-linux.sh            install and start now
#        scripts/install-linux.sh --remove   stop starting at login (the venv is kept)
set -eu

repo=$(cd "$(dirname "$0")/.." && pwd)
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
entry="$autostart/clipboard-link-opener.desktop"

if [ "${1:-}" = "--remove" ]; then
    rm -f "$entry"
    echo "removed $entry"
    exit 0
fi

case "$repo" in
    *'"'* | *'\'* | *'`'* | *'$'* | *'%'*)
        echo "the checkout path contains characters a .desktop Exec line cannot hold: $repo" >&2
        exit 1
        ;;
esac

# --system-site-packages: the tray uses the distribution's GObject bindings when present.
python3 -m venv --system-site-packages "$repo/.venv"
"$repo/.venv/bin/pip" install --quiet -r "$repo/requirements.txt"

mkdir -p "$autostart"
cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=Clipboard Link Opener
Comment=Open a copied local path or URL
Exec="$repo/.venv/bin/python" "$repo/run.py"
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
echo "wrote $entry"

nohup "$repo/.venv/bin/python" "$repo/run.py" >/dev/null 2>&1 &
echo "started (log: ${XDG_CONFIG_HOME:-$HOME/.config}/clipboard_link_opener/app.log)"
