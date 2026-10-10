#!/usr/bin/env python3
"""Forge on the normal desktop: particles (B38), widgets and a visualiser (S25).

Shows desktop/particles.html in a see-through window that sits with the
desktop, one per monitor (S24: other screens show the same scenery, each
with its own widgets), and keeps them in step with Forge's
settings by watching hub/theme.js (Settings, Wallpapers and the GNOME
extension's own settings page rewrite it on every change). It runs while any
of Settings > On the desktop's three switches is on (particles, widgets, a
visualiser) and quits when all three are off.

It needs nothing of Forge's but its files: Forge's window and apps may be
closed. What the widgets show comes from here, through the page's "desktop"
message handler (desktop/forge_desktop.js is the other side):
  stats     CPU, memory, battery, network, disks, Jan: shared/forge_sysinfo.py
  players   every media player (MPRIS): shared/forge_mpris.py
  levels    the sound for the visualiser: cava, through
            shared/forge_music.SystemLevels, only while something draws
  save      each screen's widget layout, ~/.config/forge/desktop-widgets.json
            for the main one and desktop-widgets-<n>.json for the others
  rects     where the window takes clicks; everywhere else they pass through

Where it may run, because an ordinary window would float over everything:
  GNOME     only when started by the Forge GNOME extension
            (desktop/gnome-extension/forge-desktop-particles@forge), which
            sets FORGE_DESKTOP_PARTICLES_HOST=gnome-extension and keeps the
            window below all others, on every workspace.
  Hyprland  as a layer below the windows through GtkLayerShell, only while
            On the desktop > On Hyprland too is also on (off by default,
            the owner 2026-09-28). Hyprland's config starts
            `python3 ~/Forge/desktop/particles.py --watch`, which waits
            cheaply for the switches and runs the desktop as its child.
Anywhere else it says why and exits with status 2.

`python3 desktop/particles.py --edit` asks the running one to start editing
its widgets (through a file it watches; the GNOME extension's top bar menu
does the same).

Like forge.py (B34) it rests when the desktop is fully covered: GNOME sends
fully-obscured a few seconds after, and unmapping the page makes WebKit stop
drawing until any of it shows again.
Task 60 / overhaul (2026-10-07): the GNOME extension also says how much of
each screen's desktop shows (UNDER_FILE "shown"); under a tenth rests like
fully covered, under 35 % draws the reduced power tier (shared/forge_power.js,
passed to the page as theme["shown"]), and the whole machine's CPU load
(shared/forge_gpuload.py CpuBusy) eases it like other programs' GPU load. It wakes for an alarm or timer that is
due, so those still ring.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

FORGE = Path(__file__).resolve().parents[1]
SHARED = FORGE / "shared"
PAGE = FORGE / "desktop" / "particles.html"
THEME_JS = FORGE / "hub" / "theme.js"
POWER_SUPPLY = Path("/sys/class/power_supply")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "forge"
LAYOUT = CONFIG / "desktop-widgets.json"
PID_FILE = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "forge-desktop.pid"
# --edit writes this file and the running one watches it. Not a signal:
# WebKit's JavaScript engine stops threads with SIGUSR1 during garbage
# collection, and taking that signal deadlocked the window (S26).
EDIT_FILE = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "forge-desktop-edit"
# The GNOME extension writes which screens a maximised or full-screen window
# hides ({"0": true, ...}); those rest like fully covered ones.
UNDER_FILE = (
    Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "forge-desktop-under.json"
)
LEVELS_GRACE = 6.0  # seconds without an ask before cava stops
WATCH_POLL = 2.0  # --watch: seconds between looks at the switches
WATCH_BACKOFF = 30.0  # --watch: rest after a desktop that quit at once


def host(env: dict) -> str | None:
    """How this window can sit with the desktop, or None when it can't."""
    if env.get("FORGE_DESKTOP_PARTICLES_HOST") == "gnome-extension":
        return "gnome-extension"
    desktop = env.get("XDG_CURRENT_DESKTOP", "").lower()
    if "hyprland" in desktop or env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "layer-shell"
    return None


def read_theme(path: Path = THEME_JS) -> dict:
    """hub/theme.js is `window.FORGE_THEME = {...};`; {} when unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (OSError, ValueError):
        return {}


def on_battery(root: Path = POWER_SUPPLY) -> bool:
    """True when the mains adapter reports unplugged; False when plugged in
    or when there is no adapter to ask (a desktop machine)."""
    for supply in sorted(root.glob("*")):
        try:
            if (supply / "type").read_text().strip() == "Mains":
                return (supply / "online").read_text().strip() == "0"
        except OSError:
            continue
    return False


def wanted(theme: dict, where: str | None = None) -> bool:
    """Any of the three desktop switches. The GNOME extension's _wanted()
    reads settings.json with the same rule. On Hyprland ("layer-shell") the
    On Hyprland too switch must be on as well."""
    if where == "layer-shell" and theme.get("desktop_on_hyprland") is not True:
        return False
    return (
        theme.get("desktop_particles") is True
        or theme.get("desktop_widgets") is True
        or theme.get("desktop_visualiser", "off") not in ("off", None, "")
    )


def read_under(path: Path = UNDER_FILE) -> dict[int, bool]:
    """The extension's {screen: hidden by a maximised window}; {} when absent."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {int(k): v is True for k, v in data.items() if str(k).isdigit()}


def read_shown(path: Path = UNDER_FILE) -> dict[int, float]:
    """How much of each screen's desktop shows, {screen: 0..1}, from the
    extension's "shown" entry; {} when absent. A screen it does not name
    counts as fully shown. Picks the reduced tier (shared/forge_power.js)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    shown = data.get("shown") if isinstance(data, dict) else None
    screens = shown.get("screens") if isinstance(shown, dict) else None
    if not isinstance(screens, dict):
        return {}
    return {
        int(k): min(1.0, max(0.0, float(v)))
        for k, v in screens.items()
        if str(k).isdigit() and isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def resting(theme: dict, covered: bool) -> bool:
    """Whether to stop drawing: only while covered, and only when the
    setting "Only while the desktop shows" is on (the default)."""
    return covered and theme.get("desktop_particles_rest") is not False


def load_layout(path: Path = LAYOUT) -> dict | None:
    """The desktop's widget layout as the page saved it, or None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(data, dict) and isinstance(data.get("pages"), list)
    return data if ok else None


def save_layout(data, path: Path = LAYOUT) -> bool:
    """Keep what the page sent, if it looks like a layout; written whole
    through a temporary file so a crash never leaves half a file."""
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        return False
    text = json.dumps(data)
    if len(text) > 2_000_000:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def alarm_due(layout: dict | None, now: datetime) -> bool:
    """An Alarms widget has an alarm set for this minute that has not rung
    today, or a timer that has run out: the page must be awake to ring it."""
    if not layout:
        return False
    hm = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    ms = now.timestamp() * 1000
    for page in layout.get("pages") or []:
        for item in (page or {}).get("items") or []:
            if not isinstance(item, dict) or item.get("kind") != "alarms":
                continue
            o = item.get("o") or {}
            for a in o.get("alarms") or []:
                if a.get("on", True) and a.get("t") == hm and a.get("last") != today:
                    return True
            for t in o.get("timers") or []:
                if isinstance(t.get("end"), (int, float)) and t["end"] <= ms:
                    return True
    return False


def clean_rects(rects, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """The page's click areas, as whole pixels inside the window; anything
    malformed is dropped rather than trusted."""
    out = []
    for r in rects if isinstance(rects, list) else []:
        if not (isinstance(r, list) and len(r) == 4):
            continue
        try:
            x, y, w, h = (int(round(float(v))) for v in r)
        except (TypeError, ValueError):
            continue
        x, y = max(0, x), max(0, y)
        w, h = min(w, width - x), min(h, height - y)
        if w > 0 and h > 0:
            out.append((x, y, w, h))
    return out[:200]


TITLE = "forge-desktop-particles"


def window_title(screen: int) -> str:
    """Screen 0 keeps the old title, so an extension still loaded from before
    (it adopts windows by owner, not title) places it as it always did."""
    return TITLE if screen == 0 else f"{TITLE} #{screen}"


def screen_of(title: str) -> int | None:
    """The screen number in a window title made by window_title()."""
    if title == TITLE:
        return 0
    head, sep, tail = title.rpartition(" #")
    return int(tail) if head == TITLE and sep and tail.isdigit() else None


def layout_path(screen: int, base: Path = LAYOUT) -> Path:
    """Each screen's own widget layout; screen 0 keeps the first file."""
    return base if screen == 0 else base.with_name(f"{base.stem}-{screen}{base.suffix}")


def screen_count(theme: dict, monitors: int) -> int:
    """How many desktop windows: one per monitor (S24, the owner: other
    screens show the scenery), or only the main one when "On every screen"
    is off."""
    if monitors < 1:
        return 1
    return monitors if theme.get("desktop_all_screens", True) is not False else 1


def settings_command(where: str, root: Path = FORGE) -> list[str]:
    """What the desktop's ⚙ runs: the GNOME extension's own settings page,
    which works without Forge (and without logging out after an update:
    it is read fresh each time), else Forge's Settings, else (the Forge
    Desktop release, which has no Forge) its own settings window."""
    if where == "gnome-extension":
        return ["gnome-extensions", "prefs", "forge-desktop-particles@forge"]
    if (root / "forge.py").exists():
        return [sys.executable, str(root / "forge.py"), "--app", "settings"]
    return [sys.executable, str(root / "desktop" / "start.py"), "--settings"]


def ask_to_edit(pid_file: Path = PID_FILE, edit_file: Path = EDIT_FILE) -> int:
    """--edit: tell the running one to start editing its widgets."""
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # only whether it is alive
        edit_file.write_text(str(time.time()))
    except (OSError, ValueError):
        print("Forge on the desktop is not running.", file=sys.stderr)
        return 1
    return 0


def session_alive(env=os.environ) -> bool:
    """Whether the Wayland session --watch was started in is still there:
    its socket, and Hyprland's own instance folder when it names one. Both
    go when Hyprland quits, so a watcher left behind ends by itself."""
    runtime = Path(env.get("XDG_RUNTIME_DIR") or "/tmp")
    if not (runtime / (env.get("WAYLAND_DISPLAY") or "wayland-0")).exists():
        return False
    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE")
    return not signature or (runtime / "hypr" / signature).is_dir()


def run_child() -> int:
    """The desktop itself, as a child --watch waits on. Stopping the watcher
    stops the child the gentle way, so it ends cava and its PID file."""
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve())])
    try:
        return child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


def watch(
    where: str,
    alive=session_alive,
    read=read_theme,
    start=run_child,
    sleep=time.sleep,
) -> int:
    """--watch, Hyprland's autostart: a small file read every two seconds
    until the switches want the desktop, then the desktop as a child until it
    quits (a switch went off), then back to waiting. A desktop that quits at
    once (no display, a crash) is retried after a longer rest, not in a
    tight loop."""
    while alive():
        if wanted(read(), where):
            began = time.monotonic()
            start()
            if time.monotonic() - began < 5:
                sleep(WATCH_BACKOFF)
        sleep(WATCH_POLL)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--edit" in args:
        return ask_to_edit()
    where = host(dict(os.environ))
    if "--watch" in args:
        if where != "layer-shell":
            print(
                "--watch is for Hyprland; on GNOME the Forge extension "
                "starts the desktop.",
                file=sys.stderr,
            )
            return 2
        signal.signal(signal.SIGTERM, lambda *_a: sys.exit(0))
        return watch(where)
    if where is None:
        print(
            "Forge on the desktop needs the Forge GNOME extension on GNOME, "
            "or Hyprland; an ordinary window would sit over every other window.",
            file=sys.stderr,
        )
        return 2
    if not wanted(read_theme(), where):
        return 0
    return run(where)


def run(where: str) -> int:  # pragma: no cover - the live windows
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    import cairo
    from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2

    sys.path.insert(0, str(SHARED))
    import forge_gpuload
    import forge_sysinfo

    if where == "layer-shell":
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
    else:
        GtkLayerShell = None

    display = Gdk.Display.get_default()
    # S24: one window per monitor, each with its own widgets; the sound,
    # the numbers and the players are read once and shared
    screens: dict[int, dict] = {}
    state: dict = {
        "levels": None,
        "levels_on": set(),
        "levels_at": 0.0,
        "players_busy": False,
        "stats_sample": None,
        "net": forge_sysinfo.NetMeter(),
        "slow": {},
        "slow_at": 0.0,
        "awake_until": 0.0,
        "under": read_under(),
        "shown": read_shown(),
    }

    def js(view, script: str) -> bool:
        view.evaluate_javascript(script, -1, None, None, None, None, None)
        return False

    def tell(message: dict, only: int | None = None) -> None:
        """Send to the pages (or one screen's) from any thread."""
        script = f"window.forgeDesktop && window.forgeDesktop({json.dumps(message)});"

        def go():
            for n, sc in list(screens.items()):
                if only is None or n == only:
                    js(sc["view"], script)
            return False

        GLib.idle_add(go)

    def apply_input(sc: dict):
        gdk_win = sc["win"].get_window()
        if gdk_win is None:
            return
        size = sc["win"].get_size()
        region = cairo.Region()
        for x, y, w, h in clean_rects(sc["rects"], size.width, size.height):
            region.union(cairo.RectangleInt(x, y, w, h))
        gdk_win.input_shape_combine_region(region, 0, 0)

    def show_or_rest(sc: dict):
        hidden = sc["covered"] or state["under"].get(sc["n"], False)
        rest = resting(read_theme(), hidden) and not sc["editing"]
        if time.monotonic() < state["awake_until"]:
            rest = False
        sc["view"].set_child_visible(not rest)

    # --- the pages' asks ------------------------------------------------
    def levels_push(event: dict) -> None:
        if event.get("type") == "levels":
            tell({"levels": event.get("v")})

    def levels(n: int, on: bool, fps: int = 30) -> None:
        if on:
            state["levels_on"].add(n)
            state["levels_at"] = time.monotonic()
            if state["levels"] is None:
                import forge_music

                state["levels"] = forge_music.SystemLevels()
            state["levels"].start(levels_push, 32, max(10, min(60, int(fps))))
        else:
            state["levels_on"].discard(n)
            if not state["levels_on"] and state["levels"] is not None:
                state["levels"].stop()

    def players() -> None:
        if state["players_busy"]:
            return
        state["players_busy"] = True

        def work():
            try:
                import forge_mpris

                tell({"players": forge_mpris.players()})
            finally:
                state["players_busy"] = False

        threading.Thread(target=work, daemon=True).start()

    def media(msg: dict) -> None:
        bus, op = str(msg.get("bus") or ""), str(msg.get("op") or "")
        value = msg.get("value")
        value = float(value) if isinstance(value, (int, float)) else None

        def work():
            import forge_mpris

            forge_mpris.control(bus, op, value)
            time.sleep(0.3)
            tell({"players": forge_mpris.players()})

        threading.Thread(target=work, daemon=True).start()

    def notify(text: str) -> None:
        try:
            subprocess.Popen(
                [
                    "notify-send",
                    "-a",
                    "Forge",
                    "-i",
                    "alarm-symbolic",
                    "Forge",
                    text[:200],
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def on_message(n: int, result):
        sc = screens.get(n)
        if sc is None:
            return
        value = result.get_js_value() if hasattr(result, "get_js_value") else result
        try:
            msg = json.loads(value.to_string())
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        if "rects" in msg:
            sc["rects"] = msg["rects"]
            apply_input(sc)
        if "editing" in msg:
            sc["editing"] = bool(msg["editing"])
            if sc["editing"]:
                sc["win"].present()  # the keyboard, for Esc and the widgets' boxes
            show_or_rest(sc)
        if "save" in msg and save_layout(msg["save"], layout_path(n)):
            sc["layout"] = msg["save"]
        if isinstance(msg.get("levels"), dict):
            levels(
                n, bool(msg["levels"].get("on")), int(msg["levels"].get("fps") or 30)
            )
        if msg.get("players"):
            players()
        if isinstance(msg.get("media"), dict):
            media(msg["media"])
        if isinstance(msg.get("notify"), str):
            notify(msg["notify"])
        if msg.get("settings"):
            try:
                subprocess.Popen(
                    settings_command(where),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                pass
        if msg.get("ready"):
            push_stats()
            players()

    # --- one screen -------------------------------------------------------
    def make_screen(n: int, monitor) -> dict:
        win = Gtk.Window(title=window_title(n))
        win.set_decorated(False)
        win.set_app_paintable(True)
        # the widgets take typing (notes, a weather place, an alarm); the
        # window only has input where a widget is, so it can't steal clicks
        win.set_accept_focus(True)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        visual = win.get_screen().get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)
        if GtkLayerShell is not None:
            GtkLayerShell.init_for_window(win)
            # BOTTOM, not BACKGROUND: Hyprland shells draw their wallpaper
            # on the background layer, and one mapped after this window
            # would cover it. BOTTOM is still below every window.
            GtkLayerShell.set_layer(win, GtkLayerShell.Layer.BOTTOM)
            if monitor is not None:
                GtkLayerShell.set_monitor(win, monitor)
            for edge in (
                GtkLayerShell.Edge.TOP,
                GtkLayerShell.Edge.BOTTOM,
                GtkLayerShell.Edge.LEFT,
                GtkLayerShell.Edge.RIGHT,
            ):
                GtkLayerShell.set_anchor(win, edge, True)
            GtkLayerShell.set_exclusive_zone(win, -1)
            if hasattr(GtkLayerShell, "set_keyboard_mode"):
                GtkLayerShell.set_keyboard_mode(
                    win, GtkLayerShell.KeyboardMode.ON_DEMAND
                )
        elif monitor is not None:
            # the GNOME extension moves it over its monitor; a first guess
            area = monitor.get_geometry()
            win.set_default_size(area.width, area.height)
            win.move(area.x, area.y)

        content = WebKit2.UserContentManager()
        content.register_script_message_handler("desktop")
        # the saved layout is in the page before its scripts run, so the
        # widgets read it as their store without waiting
        boot = {"layout": load_layout(layout_path(n)), "host": where, "screen": n}
        content.add_script(
            WebKit2.UserScript.new(
                f"window.FORGE_DESKTOP = {json.dumps(boot)};",
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        view = WebKit2.WebView.new_with_user_content_manager(content)
        view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        settings = view.get_settings()
        settings.set_property("allow-file-access-from-file-urls", True)
        settings.set_property("allow-universal-access-from-file-urls", True)
        # an alarm rings through Web Audio with nobody pressing anything first
        settings.set_property("media-playback-requires-user-gesture", False)
        win.add(view)
        sc = {
            "n": n,
            "win": win,
            "view": view,
            "rects": [],
            "editing": False,
            "covered": False,
            "layout": boot["layout"],
        }
        content.connect(
            "script-message-received::desktop", lambda _m, r: on_message(n, r)
        )
        # no rects yet: every click goes to the desktop below
        win.connect("realize", lambda *_a: apply_input(sc))

        def on_visibility(_win, event):
            sc["covered"] = event.state == Gdk.VisibilityState.FULLY_OBSCURED
            show_or_rest(sc)
            return False

        win.add_events(Gdk.EventMask.VISIBILITY_NOTIFY_MASK)
        win.connect("visibility-notify-event", on_visibility)
        # closing one screen's window (a monitor went away) is not quitting
        win.connect("delete-event", lambda *_a: True)
        view.load_uri(PAGE.as_uri())
        win.show_all()
        return sc

    def sync_screens(*_a) -> bool:
        """One window per monitor now there (or only the first when the
        setting says so); made and closed as monitors come and go."""
        count = screen_count(read_theme(), display.get_n_monitors())
        monitors = [display.get_monitor(k) for k in range(display.get_n_monitors())]
        primary = display.get_primary_monitor() or (monitors[0] if monitors else None)
        order = [primary] + [m for m in monitors if m != primary]
        for n in range(count):
            if n not in screens:
                screens[n] = make_screen(n, order[n] if n < len(order) else None)
        for n in [k for k in screens if k >= count]:
            sc = screens.pop(n)
            state["levels_on"].discard(n)
            sc["win"].destroy()
        return False

    def push_stats():
        if read_theme().get("desktop_widgets") is not True:
            return True
        now = time.monotonic()
        if now - state["slow_at"] > 30:
            state["slow_at"] = now
            state["slow"] = {
                "disks": forge_sysinfo.disks(),
                "jan": forge_sysinfo.jan_status(),
                "temps": forge_sysinfo.temperatures(),
            }
        stats, state["stats_sample"] = forge_sysinfo.snapshot(
            state["stats_sample"], state["net"], state["slow"]
        )
        tell({"stats": stats})
        return True

    GLib.timeout_add_seconds(3, push_stats)

    def housekeeping():
        # cava stops once nothing has asked for a while (a page went away,
        # or the visualiser was turned off without saying so)
        if (
            state["levels"] is not None
            and time.monotonic() - state["levels_at"] > LEVELS_GRACE
        ):
            state["levels"].stop()
            state["levels_on"].clear()
        # an alarm or timer due while a page rests: wake the pages to ring it
        due = any(alarm_due(sc["layout"], datetime.now()) for sc in screens.values())
        if due:
            state["awake_until"] = time.monotonic() + 90
        elif state["awake_until"] and time.monotonic() > state["awake_until"]:
            state["awake_until"] = 0.0
        else:
            return True
        for sc in screens.values():
            show_or_rest(sc)
        return True

    GLib.timeout_add_seconds(5, housekeeping)

    pending = {"id": 0}
    power: dict = {
        "on_battery": None,
        "gpu": 0,
        "reason": "",
        "battery": None,
        "low": None,
    }
    gpu_load = forge_gpuload.GpuLoad()

    def reload_theme():
        pending["id"] = 0
        theme = read_theme()
        # hub/theme.js can't know the plug; the page picks battery_fps from it
        theme["on_battery"] = bool(power["on_battery"])
        # easing off (shared/forge_power.js): the GPU's level and the charge
        theme["gpu_level"] = power["gpu"]
        theme["gpu_reason"] = power["reason"]
        theme["battery"] = power["battery"]
        if not wanted(theme, where):
            Gtk.main_quit()
            return False
        sync_screens()
        for sc in screens.values():
            # each screen's own share: a mostly covered one draws the reduced
            # tier (shared/forge_power.js)
            theme["shown"] = state["shown"].get(sc["n"], 1.0)
            show_or_rest(sc)
            js(
                sc["view"],
                f"window.forgeTheme && window.forgeTheme({json.dumps(theme)});",
            )
        return False

    def on_theme_changed(*_args):
        # theme.js is replaced by a rename; let a burst of events settle
        if not pending["id"]:
            pending["id"] = GLib.timeout_add(300, reload_theme)

    monitor_file = Gio.File.new_for_path(str(THEME_JS)).monitor_file(
        Gio.FileMonitorFlags.WATCH_MOVES, None
    )
    monitor_file.connect("changed", on_theme_changed)

    def check_power():
        # sysfs can't be watched for changes; a few tiny reads every 5 s,
        # and the other programs' GPU load (shared/forge_gpuload.py)
        now = on_battery()
        load = gpu_load.read()
        level, reason = load["level"], load.get("reason", "")
        pct = forge_gpuload.battery_percent(POWER_SUPPLY)
        try:
            raw = read_theme().get("battery_saver", 20)
            saver = float(20 if raw is None else raw)
        except (TypeError, ValueError):
            saver = 20.0
        low = pct is not None and pct <= saver
        key = (power["on_battery"], power["gpu"], power["reason"], power["low"])
        if (now, level, reason, low) != key:
            power.update(on_battery=now, gpu=level, reason=reason, battery=pct, low=low)
            reload_theme()
        return True

    def on_under(*_args):
        state["under"] = read_under()
        for sc in screens.values():
            show_or_rest(sc)
        shown = read_shown()
        if shown != state["shown"]:
            state["shown"] = shown
            reload_theme()

    under_monitor = Gio.File.new_for_path(str(UNDER_FILE)).monitor_file(
        Gio.FileMonitorFlags.NONE, None
    )
    under_monitor.connect("changed", on_under)

    GLib.timeout_add_seconds(5, check_power)

    def start_editing(*_a):
        tell({"edit": True}, only=0)

    edit_monitor = Gio.File.new_for_path(str(EDIT_FILE)).monitor_file(
        Gio.FileMonitorFlags.NONE, None
    )

    def on_edit_asked(_m, _f, _o, event):
        if event in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
        ):
            start_editing()

    edit_monitor.connect("changed", on_edit_asked)

    def stop(*_a):
        if state["levels"] is not None:
            state["levels"].stop()
        Gtk.main_quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    # a monitor plugged in or out: a window more or less
    display.connect("monitor-added", lambda *_a: GLib.idle_add(sync_screens))
    display.connect("monitor-removed", lambda *_a: GLib.idle_add(sync_screens))
    try:
        PID_FILE.write_text(str(os.getpid()))
    except OSError:
        pass
    sync_screens()
    Gtk.main()
    try:
        if PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
