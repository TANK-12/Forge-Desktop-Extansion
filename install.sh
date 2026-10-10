#!/bin/sh
# Forge Desktop for Linux: copies this folder to ~/.local/share/forge-desktop
# and sets it up for GNOME (its extension) or Hyprland (one config line).
# Nothing goes system-wide and nothing needs sudo: a missing package is
# named with the command that installs it, and the install stops there.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/forge-desktop"
EXT_ID="forge-desktop-particles@forge"
EXT="$DATA/gnome-shell/extensions/$EXT_ID"
SETTINGS="${FORGE_CONFIG:-$HOME/.config/forge/settings.json}"
DESKTOP=$(printf '%s' "${XDG_CURRENT_DESKTOP:-}" | tr '[:upper:]' '[:lower:]')
case "$DESKTOP" in
  *gnome*) KIND=gnome ;;
  *hyprland*) KIND=hyprland ;;
  *) if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then KIND=hyprland; else KIND=other; fi ;;
esac
# The extension starts the desktop with /usr/bin/python3, the system's own
# (it has the GTK bindings; a python3 earlier on PATH may not), so that is
# the one checked and used here. FORGE_DESKTOP_PYTHON names another (tests).
PYTHON="${FORGE_DESKTOP_PYTHON:-/usr/bin/python3}"

# --- what it needs: the system's Python 3 with GTK 3 and WebKit2GTK 4.1 ---
FAMILY=other
if [ -r /etc/os-release ]; then
  IDS=$(. /etc/os-release; printf '%s %s' "${ID:-}" "${ID_LIKE:-}")
  case " $IDS " in
    *" arch "*) FAMILY=arch ;;
    *" debian "*|*" ubuntu "*) FAMILY=debian ;;
    *" fedora "*|*" rhel "*) FAMILY=fedora ;;
    *suse*) FAMILY=suse ;;
  esac
fi
pkg() {  # pkg <arch> <debian> <fedora> <suse>: the name on this system
  case "$FAMILY" in
    arch) echo "$1" ;; debian) echo "$2" ;; fedora) echo "$3" ;; suse) echo "$4" ;;
    *) echo "$1 (Arch's name)" ;;
  esac
}
has() {  # has <namespace> <version>: a GObject library Python can load
  "$PYTHON" -c "import gi; gi.require_version('$1', '$2'); from gi.repository import $1" >/dev/null 2>&1
}
MISSING=""
[ -x "$PYTHON" ] || MISSING="$MISSING $(pkg python python3 python3 python3)"
"$PYTHON" -c 'import gi' >/dev/null 2>&1 || MISSING="$MISSING $(pkg python-gobject python3-gi python3-gobject python3-gobject)"
has Gtk 3.0 || MISSING="$MISSING $(pkg gtk3 gir1.2-gtk-3.0 gtk3 typelib-1_0-Gtk-3_0)"
has WebKit2 4.1 || MISSING="$MISSING $(pkg webkit2gtk-4.1 gir1.2-webkit2-4.1 webkit2gtk4.1 typelib-1_0-WebKit2-4_1)"
if [ "$KIND" = hyprland ]; then
  has GtkLayerShell 0.1 || MISSING="$MISSING $(pkg gtk-layer-shell gir1.2-gtklayershell-0.1 gtk-layer-shell typelib-1_0-GtkLayerShell-0_1)"
fi
if [ -n "$MISSING" ]; then
  echo "Forge Desktop needs these packages first:$MISSING"
  case "$FAMILY" in
    arch) echo "  sudo pacman -S --needed$MISSING" ;;
    debian) echo "  sudo apt install$MISSING" ;;
    fedora) echo "  sudo dnf install$MISSING" ;;
    suse) echo "  sudo zypper install$MISSING" ;;
  esac
  echo "Then run this again. Nothing was changed."
  exit 1
fi
# --- GNOME loads the extension only on the versions its metadata.json names ---
if [ "$KIND" = gnome ]; then
  SHELL_VERSION=$(gnome-shell --version 2>/dev/null | sed -n 's/^GNOME Shell \([0-9][0-9]*\).*/\1/p')
  if [ -n "$SHELL_VERSION" ] && ! "$PYTHON" - "$HERE/desktop/gnome-extension/$EXT_ID/metadata.json" "$SHELL_VERSION" <<'PY'
import json, sys
known = json.load(open(sys.argv[1], encoding="utf-8"))["shell-version"]
if sys.argv[2] not in known:
    print(f"Forge Desktop's GNOME extension runs on GNOME {', '.join(known)}; this is GNOME {sys.argv[2]}.")
    print("Nothing was changed.")
    sys.exit(1)
PY
  then
    exit 1
  fi
fi
OPTIONAL=""
command -v cava >/dev/null 2>&1 || OPTIONAL="$OPTIONAL cava (the music visualiser)"
command -v notify-send >/dev/null 2>&1 || OPTIONAL="$OPTIONAL, $(pkg libnotify libnotify-bin libnotify libnotify-tools) (alarm and timer notifications)"

# --- the files: copied beside, then swapped in, so a failed copy changes nothing ---
mkdir -p "$DATA"
rm -rf "$DEST.new"
mkdir "$DEST.new"
cp -R "$HERE/desktop" "$HERE/shared" "$HERE/uninstall.sh" "$HERE/README.txt" "$DEST.new/"
mkdir -p "$DEST.new/hub"
if [ -d "$DEST" ]; then
  rm -rf "$DEST.old"
  mv "$DEST" "$DEST.old"
fi
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"
echo "Copied to $DEST"

# --- a first install shows something: particles and widgets on. An update
# keeps the settings and makes hub/theme.js (what the desktop reads its
# switches from) again, since the fresh copy's hub/ is empty ---
if [ ! -e "$SETTINGS" ]; then
  "$PYTHON" "$DEST/shared/forge_theme.py" --set desktop_particles=true >/dev/null
  "$PYTHON" "$DEST/shared/forge_theme.py" --set desktop_widgets=true >/dev/null
  echo "Particles and widgets are on (settings in $SETTINGS)."
elif ! "$PYTHON" - "$DEST" >/dev/null <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "shared"))
import forge_theme
forge_theme.apply(Path(sys.argv[1]) / "hub")
PY
then
  echo "Your settings could not be read; change any setting once to start the desktop."
fi

case "$KIND" in
  gnome)
    if [ -L "$EXT" ]; then
      echo "The GNOME extension at $EXT is a link to another copy; it was left as it is."
    else
      rm -rf "$EXT"
      mkdir -p "$EXT"
      cp -R "$DEST/desktop/gnome-extension/$EXT_ID/." "$EXT/"
      printf '%s\n' "$DEST" > "$EXT/root.txt"
    fi
    "$PYTHON" - "$EXT_ID" <<'PY' || true
import ast, subprocess, sys
ext = sys.argv[1]
key = ["org.gnome.shell", "enabled-extensions"]
out = subprocess.run(["gsettings", "get", *key], capture_output=True, text=True).stdout.strip()
out = out[3:].strip() if out.startswith("@as") else out
names = list(ast.literal_eval(out)) if out else []
if ext not in names:
    subprocess.run(["gsettings", "set", *key, str(names + [ext])], check=False)
PY
    gnome-extensions enable "$EXT_ID" >/dev/null 2>&1 || true
    echo "The GNOME extension is installed and turned on. Log out and back in once to start it."
    ;;
  hyprland)
    "$PYTHON" "$DEST/shared/forge_theme.py" --set desktop_on_hyprland=true >/dev/null
    echo "On Hyprland: add this line to ~/.config/hypr/hyprland.conf, then log in again"
    echo "(or run the command after = now):"
    echo "  exec-once = $PYTHON $DEST/desktop/particles.py --watch"
    ;;
  *)
    echo "This desktop (${XDG_CURRENT_DESKTOP:-unknown}) is not supported yet: Forge Desktop"
    echo "runs on GNOME and Hyprland. The files are in $DEST."
    ;;
esac
if [ -n "$OPTIONAL" ]; then
  echo "Optional, not found:${OPTIONAL#,}"
fi
