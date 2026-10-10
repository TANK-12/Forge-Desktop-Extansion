"""Media players elsewhere on the desktop, for the hub's Now playing.

MPRIS is the D-Bus interface every Linux media player speaks (Firefox,
Spotify, mpv, VLC...). forge.py uses this on a worker thread: players() lists
what they are playing, control() sends play/pause/next/previous/seek and
Raise, which brings the player's window forward where the player supports it
("Go to it"). Forge's own web views also appear on the bus as WebKit players;
the hub hears those apps directly (shared/forge_media.js), so they are left
out here.

Runs on the system python with PyGObject (forge.py's), never an app venv.
"""

from __future__ import annotations

import os
import subprocess

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
TIMEOUT_MS = 800

# The hub's command words -> MPRIS methods; nothing else is ever called.
METHODS = {
    "toggle": "PlayPause",
    "play": "Play",
    "pause": "Pause",
    "next": "Next",
    "prev": "Previous",
    "raise": "Raise",
}

STATES = {"Playing": "playing", "Paused": "paused", "Stopped": "stopped"}


# Not players of their own: Forge's WebKit views (the hub hears those apps
# directly) and playerctld, which mirrors whichever player spoke last.
NOT_PLAYERS = (PREFIX + "org.webkit.", PREFIX + "playerctld")


def foreign(bus: str) -> bool:
    """A player on the bus that is a real player outside Forge."""
    return bus.startswith(PREFIX) and not bus.startswith(NOT_PLAYERS)


def track(meta) -> dict:
    """MPRIS Metadata (already unpacked) -> the popup's title/artist/art/length."""
    meta = meta if isinstance(meta, dict) else {}
    artist = meta.get("xesam:artist") or ""
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(str(a) for a in artist if a)
    art = str(meta.get("mpris:artUrl") or "")
    if not art.startswith(("http://", "https://", "file://")):
        art = ""
    try:
        length = int(meta.get("mpris:length") or 0) / 1_000_000
    except (TypeError, ValueError):
        length = 0
    return {
        "title": str(meta.get("xesam:title") or ""),
        "artist": str(artist),
        "artwork": art,
        "duration": round(length, 3) if length > 0 else 0,
    }


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _names(conn) -> list[str]:
    reply = conn.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "ListNames",
        None,
        GLib.VariantType.new("(as)"),
        Gio.DBusCallFlags.NONE,
        TIMEOUT_MS,
        None,
    )
    return [n for n in reply.unpack()[0] if foreign(n)]


def _get_all(conn, bus: str, iface: str) -> dict:
    reply = conn.call_sync(
        bus,
        PATH,
        "org.freedesktop.DBus.Properties",
        "GetAll",
        GLib.Variant("(s)", (iface,)),
        GLib.VariantType.new("(a{sv})"),
        Gio.DBusCallFlags.NONE,
        TIMEOUT_MS,
        None,
    )
    return reply.unpack()[0]


def players() -> list[dict]:
    """Every foreign player: bus, name, state, track, position, what it can do.
    A player that does not answer in time is skipped, never waited on."""
    try:
        conn = _bus()
        names = _names(conn)
    except GLib.Error:
        return []
    found = []
    for bus in names:
        try:
            root = _get_all(conn, bus, ROOT_IFACE)
            player = _get_all(conn, bus, PLAYER_IFACE)
        except GLib.Error:
            continue
        vol = player.get("Volume")
        item = {
            "bus": bus,
            "name": str(root.get("Identity") or bus[len(PREFIX) :].split(".")[0]),
            "desktop": str(root.get("DesktopEntry") or ""),
            "state": STATES.get(str(player.get("PlaybackStatus")), "stopped"),
            "position": max(0, int(player.get("Position") or 0)) / 1_000_000,
            "volume": vol if isinstance(vol, (int, float)) else 1.0,
            "canRaise": bool(root.get("CanRaise")) or _pid(conn, bus) > 0,
            "canNext": bool(player.get("CanGoNext")),
            "canPrev": bool(player.get("CanGoPrevious")),
            "canSeek": bool(player.get("CanSeek")),
        }
        item.update(track(player.get("Metadata")))
        found.append(item)
    return found


def control(bus: str, op: str, value: float | None = None) -> bool:
    """Send one command; True when the player accepted it."""
    if not foreign(bus):
        return False
    try:
        conn = _bus()
        if op == "raise" and focus(
            conn, bus, str(_root_prop(conn, bus, "DesktopEntry"))
        ):
            return True
        if op in METHODS:
            iface = ROOT_IFACE if op == "raise" else PLAYER_IFACE
            conn.call_sync(
                bus,
                PATH,
                iface,
                METHODS[op],
                None,
                None,
                Gio.DBusCallFlags.NONE,
                TIMEOUT_MS,
                None,
            )
            return True
        if op == "seek" and value is not None:
            meta = _get_all(conn, bus, PLAYER_IFACE).get("Metadata") or {}
            trackid = meta.get("mpris:trackid")
            if not trackid:
                return False
            conn.call_sync(
                bus,
                PATH,
                PLAYER_IFACE,
                "SetPosition",
                GLib.Variant("(ox)", (trackid, int(max(0, value) * 1_000_000))),
                None,
                Gio.DBusCallFlags.NONE,
                TIMEOUT_MS,
                None,
            )
            return True
        if op == "volume" and value is not None:
            conn.call_sync(
                bus,
                PATH,
                "org.freedesktop.DBus.Properties",
                "Set",
                GLib.Variant(
                    "(ssv)",
                    (
                        PLAYER_IFACE,
                        "Volume",
                        GLib.Variant("d", max(0.0, min(1.0, value))),
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                TIMEOUT_MS,
                None,
            )
            return True
    except GLib.Error:
        return False
    return False


# --- "Go to it" --------------------------------------------------------------
# MPRIS Raise alone mostly does nothing: GNOME on Wayland refuses a window that
# asks to come forward by itself (focus-stealing prevention), and many players
# (Qobuz, Electron apps) say CanRaise false. So the window is found by the
# player's process instead and brought forward by the desktop: on Hyprland with
# hyprctl, on GNOME through the Forge desktop extension's Activate method
# (desktop/gnome-extension). MPRIS Raise stays the last resort.

SHELL_NAME = "org.forge.Desktop"
SHELL_PATH = "/org/forge/Desktop"


def _pid(conn, bus: str) -> int:
    try:
        reply = conn.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            GLib.Variant("(s)", (bus,)),
            None,
            Gio.DBusCallFlags.NONE,
            TIMEOUT_MS,
            None,
        )
        return int(reply.unpack()[0])
    except GLib.Error:
        return 0


def _root_prop(conn, bus: str, name: str):
    try:
        return _get_all(conn, bus, ROOT_IFACE).get(name) or ""
    except GLib.Error:
        return ""


def lineage(pid: int, proc: str = "/proc") -> list[int]:
    """The pid and its parents, nearest first, stopping before init. A player's
    D-Bus name can belong to a helper process while the window is its parent's."""
    chain = []
    while pid > 1 and pid not in chain and len(chain) < 8:
        chain.append(pid)
        try:
            with open(f"{proc}/{pid}/stat") as f:
                # the name in brackets may hold spaces; the parent is after it
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return chain


def focus(conn, bus: str, desktop: str = "") -> bool:
    """Bring the player's window forward; True when the desktop did it."""
    pids = lineage(_pid(conn, bus))
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        for pid in pids:
            try:
                out = subprocess.run(
                    ["hyprctl", "dispatch", "focuswindow", f"pid:{pid}"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            if out.stdout.strip() == "ok":
                return True
        return False
    try:
        reply = conn.call_sync(
            SHELL_NAME,
            SHELL_PATH,
            SHELL_NAME,
            "Activate",
            GLib.Variant("(aus)", (pids, desktop)),
            None,
            Gio.DBusCallFlags.NONE,
            TIMEOUT_MS,
            None,
        )
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False  # the extension is off or older: fall back to Raise
