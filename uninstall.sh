#!/bin/sh
# Forge Desktop for Linux: stops it and removes its files and GNOME extension.
# Settings in ~/.config/forge stay (Forge uses the same folder).
set -eu
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/forge-desktop"
EXT_ID="forge-desktop-particles@forge"
EXT="$DATA/gnome-shell/extensions/$EXT_ID"
# Only the extension this install put there (its root.txt names this
# folder) is turned off and removed; a linked copy is left as it is.
if [ -d "$EXT" ] && [ ! -L "$EXT" ] && [ -f "$EXT/root.txt" ] &&
  [ "$(cat "$EXT/root.txt")" = "$DEST" ]; then
  if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions disable "$EXT_ID" >/dev/null 2>&1 || true
  fi
  rm -rf "$EXT"
  echo "Removed the GNOME extension."
fi
if [ -f "$DEST/desktop/particles.py" ]; then
  pkill -f "$DEST/desktop/particles.py" >/dev/null 2>&1 || true
  rm -rf "$DEST"
  echo "Removed $DEST."
fi
echo "On Hyprland, also take the exec-once line for Forge Desktop out of hyprland.conf."
