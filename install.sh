#!/usr/bin/env bash
# Installs Palimpsest for the current user. Does not need root.
set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin"

cd "$ROOT"
# A damaged environment, for example after a system cleaner, is rebuilt.
if [ -e .venv ] && ! .venv/bin/python -c "import textual, rich.text" >/dev/null 2>&1; then
  rm -rf -- "$ROOT/.venv"
fi
if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing --python 3.12 .venv
  uv pip install --python .venv/bin/python -e .
else
  python3 -m venv .venv
  .venv/bin/pip install -e .
fi

chmod +x bin/palimpsest install-tools.sh
mkdir -p "$BIN" "$DATA/applications" "$DATA/icons/hicolor/256x256/apps"
ln -sf "$ROOT/bin/palimpsest" "$BIN/palimpsest"
install -m644 assets/icon.png "$DATA/icons/hicolor/256x256/apps/palimpsest.png"

if command -v kitty >/dev/null 2>&1; then
  EXEC="kitty --class palimpsest --title Palimpsest --config NONE --config \"$ROOT/kitty.conf\" \"$ROOT/bin/palimpsest\" %f"
  TERMINAL=false
else
  EXEC="\"$ROOT/bin/palimpsest\" %f"
  TERMINAL=true
fi

cat > "$DATA/applications/palimpsest.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.5
Name=Palimpsest
GenericName=Investigation and ARG Workbench
Comment=Inspect, extract, decode, and hide secrets in files
Exec=$EXEC
Icon=palimpsest
Terminal=$TERMINAL
Categories=Utility;
Keywords=arg;steganography;metadata;exif;forensics;cipher;
StartupWMClass=palimpsest
StartupNotify=true
EOF

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true

echo
echo "  Palimpsest installed. Launch it from your app menu or run: palimpsest"
echo "  On Arch, ./install-tools.sh installs the external tools."
