#!/usr/bin/env bash
# Installs every external tool Palimpsest can drive.
# Repo packages go first. Each AUR package builds on its own, so one failure
# does not block the others.
set -u

REPO=(perl-image-exiftool binwalk foremost mat2 okteta sonic-visualiser sox age qpdf)
AUR=(pngcheck steghide stegseek outguess zsteg minimodem qsstv cyberchef-web)
STATUS="${XDG_STATE_HOME:-$HOME/.local/state}/palimpsest-install.txt"
mkdir -p "$(dirname "$STATUS")"
: > "$STATUS"

echo
echo "  PALIMPSEST // tool install"
echo "  ──────────────────────────"
echo

if sudo pacman -S --needed "${REPO[@]}"; then
  echo "repo ok" >> "$STATUS"
else
  echo "repo FAILED" >> "$STATUS"
fi

for pkg in "${AUR[@]}"; do
  echo
  echo "  ── $pkg"
  if yay -S --needed --answerclean None --answerdiff None "$pkg"; then
    echo "$pkg ok" >> "$STATUS"
  else
    echo "$pkg FAILED" >> "$STATUS"
  fi
done

echo
echo "  results"
echo "  ───────"
sed 's/^/  /' "$STATUS"
echo
read -r -p "  press enter to close " _
