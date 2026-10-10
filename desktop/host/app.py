"""Forge Desktop's start on Windows and macOS, its settings window on Linux
(where particles.py draws the desktop), and the plain-window preview on any
OS (Relay task 130, 2026-10-09). desktop/start.py runs main().

The order matters:
- forge_theme reads where its files live from the environment when it is
  imported, so point_theme() comes first: everything goes into Forge
  Desktop's own folder (config_dir), never into Forge's ~/.config/forge;
- on Windows the process is made DPI aware before any window or monitor is
  read, so every number is in real pixels on every screen;
- the providers are picked before the Core is made, because a kind of data
  nothing can read here (no loopback sound device, say) is cut from the
  page's caps, and so is the scenery when Windows has no place for it
  behind the desktop icons.

One Forge Desktop runs per user. A second start (the Start menu, Terminal:
--settings, --edit, --quit) leaves its ask in a file the running one reads
every second (Core.check_asks) and exits; a --quit first waits for the
running one to end, so Install.cmd can copy over its files. On macOS
opening the app again
from Finder starts no second process: the running one gets a reopen event
(macos.py).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import core, registry
from .server import Server
from .webview_shell import BACKGROUND, Screen, WebviewShell

APP_NAME = "Forge Desktop"
ROOT = core.DESKTOP.parent  # the folder holding desktop/, shared/ and hub/
KINDS = ("stats", "players", "levels", "notify")
LOG_LIMIT = 1_000_000
QUIT_GRACE = 8.0  # seconds a quit may take before the process just ends
PREVIEW = Screen(0, 0, 1280, 800)
KEEPER_HTML = "<!doctype html><title>Forge Desktop</title>"
_HELD: list[Any] = []  # the single-instance lock, held for the process's life
NO_WEBVIEW2 = (
    "Forge Desktop needs the Microsoft Edge WebView2 Runtime, which comes with "
    "Windows 11 and most Windows 10 PCs. Install the Evergreen Runtime from "
    "Microsoft's WebView2 page and start Forge Desktop again."
)


def config_dir(os_name: str, env=os.environ, home: Path | None = None) -> Path:
    """Where Forge Desktop keeps its settings, layouts and log."""
    if env.get("FORGE_DESKTOP_HOME"):
        return Path(env["FORGE_DESKTOP_HOME"])
    home = home or Path.home()
    if os_name == "windows":
        base = env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / APP_NAME
    if os_name == "macos":
        return home / "Library" / "Application Support" / APP_NAME
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else home / ".config") / "forge-desktop"


def webview_storage(os_name: str, paths: core.Paths, env=os.environ) -> Path:
    """The web view's profile (caches of many megabytes): on Windows in the
    local folder, not the roaming one that follows a user between PCs."""
    if os_name == "windows" and env.get("LOCALAPPDATA"):
        if not env.get("FORGE_DESKTOP_HOME"):
            return Path(env["LOCALAPPDATA"]) / APP_NAME / "webview"
    return paths.config / "webview"


def point_theme(paths: core.Paths, env=os.environ) -> None:
    """forge_theme's files inside Forge Desktop's own folder; before it is imported."""
    env["FORGE_CONFIG"] = str(paths.settings)
    env["FORGE_CACHE"] = str(paths.cache)
    env["FORGE_WALLPAPERS"] = str(paths.config / "wallpapers")
    env["FORGE_DESKTOP_WALLPAPER"] = str(paths.wallpaper_state)
    env["FORGE_AUTOSTART"] = str(paths.config / "autostart.desktop")


class QuietSubprocess:
    """forge_theme's subprocess on Windows: a program it runs (ImageMagick,
    when installed) opens no console window under the windowless Python."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, *args, **kwargs):
        kwargs.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
        return subprocess.run(*args, **kwargs)


def load_theme(os_name: str):
    shared = str(ROOT / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    theme = importlib.import_module("forge_theme")
    if os_name == "windows":
        setattr(theme, "subprocess", QuietSubprocess())
    return theme


def pick_sources(reg: dict, os_name: str, anywhere: bool = False) -> tuple[dict, dict]:
    """kind -> the provider module that works here (None when none does), and
    {feature: why} for the features that lose all their providers.

    anywhere (the preview): after this OS's own providers, any other OS's
    that happens to work here (psutil and soundcard work on Linux too)."""
    if anywhere:
        reg = json.loads(json.dumps(reg))
        mine = reg["platforms"][os_name].setdefault("providers", {})
        for kind in KINDS:
            names = list(mine.get(kind, []))
            for entry in reg["platforms"].values():
                names += [
                    n
                    for n in entry.get("providers", {}).get(kind, [])
                    if n not in names
                ]
            mine[kind] = names
    sources: dict[str, Any] = {}
    cut: dict[str, str] = {}
    for kind in KINDS:
        module, why = registry.pick(reg, os_name, kind)
        sources[kind] = module
        if module is None and kind in reg.get("features", {}):
            cut[kind] = str(why)
    return sources, cut


def first_instance(paths: core.Paths, w32=None) -> bool:
    """True for the first Forge Desktop of this user (a named mutex on
    Windows, a lock on a file elsewhere)."""
    if w32 is not None:
        return w32.single_instance()
    import fcntl

    paths.config.mkdir(parents=True, exist_ok=True)
    handle = open(paths.config / "lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _HELD.append(handle)
    return True


def pass_on(paths: core.Paths, ask: str) -> None:
    paths.config.mkdir(parents=True, exist_ok=True)
    paths.ask.write_text(ask, encoding="utf-8")


STILL_RUNNING = 3  # a --quit whose running copy did not end in time


def wait_for_exit(
    paths: core.Paths,
    w32=None,
    limit: float = QUIT_GRACE + 7,
    clock: Callable[[], float] = time.monotonic,
    nap: Callable[[float], Any] = time.sleep,
) -> bool:
    """After a quit was passed on: True once the running Forge Desktop has
    ended (its lock or mutex is free again), False after `limit` seconds.
    It reads its asks every second and ends at most QUIT_GRACE seconds
    after a quit; Install.cmd and Uninstall.cmd wait on this before they
    touch its files."""
    end = clock() + limit
    while not first_instance(paths, w32):
        if clock() >= end:
            return False
        nap(0.25)
    return True


def open_log(paths: core.Paths) -> Callable[[str], None]:
    """A line-per-event log in the config folder (one older file kept); with
    the windowless Python, crashes in threads land there too."""
    path = paths.config / "forge-desktop.log"
    paths.config.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_LIMIT:
            path.replace(path.with_name(path.name + ".old"))
    except OSError:
        pass
    # pythonw has no output at all; a macOS app's goes nowhere anyone looks
    in_app = ".app/Contents/" in (sys.executable or "")
    if in_app or sys.stderr is None or sys.stdout is None:
        stream = open(path, "a", encoding="utf-8", buffering=1)
        sys.stderr = stream if in_app or sys.stderr is None else sys.stderr
        sys.stdout = stream if in_app or sys.stdout is None else sys.stdout
    lock = threading.Lock()

    def log(text: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n"
        with lock:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass

    return log


def exit_soon(
    log, delay: float = QUIT_GRACE, end: Callable[[int], Any] = os._exit
) -> threading.Timer:
    """End the process `delay` seconds after a quit even if a window never
    closes: pywebview returns only once every window it made is gone, and one
    that Explorer took down with its desktop never says so. Otherwise the
    single-instance lock would stay held and every later start would only
    leave an ask nobody reads."""

    def overdue():
        log("a window did not close in time: ending the process")
        end(0)

    timer = threading.Timer(delay, overdue)
    timer.daemon = True
    timer.start()
    return timer


def keep_login(desk, first: bool, log) -> None:
    """Start at log-in: on at the first start, and written afresh at every
    start while it is on, so an app that was moved still starts."""
    try:
        if first or desk.login_state():
            desk.set_login(True)
    except Exception as e:
        log(f"start at log-in: {e!r}")


def webview2_ready() -> bool:
    """pywebview quietly falls back to Internet Explorer's engine when
    WebView2 is missing, and that cannot draw these pages."""
    try:
        winforms = importlib.import_module("webview.platforms.winforms")
    except Exception:
        return True  # webview.start will say what is wrong
    return getattr(winforms, "renderer", "edgechromium") != "mshtml"


def tell_user(os_name: str, text: str) -> None:
    """A start-up failure the user must see (there is no console)."""
    try:
        if os_name == "windows":
            import ctypes

            getattr(ctypes, "windll").user32.MessageBoxW(None, text, APP_NAME, 0x10)
        elif os_name == "macos":
            script = f"display alert {json.dumps(APP_NAME)} message {json.dumps(text)} as critical"
            subprocess.run(["osascript", "-e", script], timeout=120, check=False)
        else:
            print(text, file=sys.stderr)
    except Exception:
        pass


class NullShell:
    """A Shell with no windows: the self-test."""

    def run_js(self, page, script, slot=None):
        pass

    def set_rects(self, page, rects):
        pass

    def present(self, page):
        pass

    def rest(self, page, resting):
        pass

    def open_settings(self):
        pass

    def screens_changed(self):
        pass

    def quit(self):
        pass


class PreviewShell(WebviewShell):
    """The page in an ordinary 1280x800 window, on any OS: for trying Forge
    Desktop out and for checking the pywebview side on Linux. No placement
    on the desktop and no click-through; closing the window quits."""

    def screens(self) -> list[Screen]:
        return [PREVIEW]

    def window_args(self, page: core.Page, screen: Screen) -> dict:
        return {
            "width": screen.w,
            "height": screen.h,
            "resizable": False,
            "background_color": BACKGROUND,
        }

    def set_rects(self, page: core.Page, rects) -> None:
        pass

    def present(self, page: core.Page) -> None:
        pass

    def rest(self, page: core.Page, resting: bool) -> None:
        pass

    def _closing(self, window):
        if not self.quitting:
            self.quit()
        return None


class LinuxSettings(core.Core):
    """The settings window on Linux, where particles.py draws the desktop
    (the GNOME extension or Hyprland starts it). Its settings are
    forge_theme's own file, and each change rewrites hub/theme.js, which
    particles.py watches, as the GNOME extension's settings page does."""

    hub = ROOT / "hub"

    def theme(self, fresh: bool = False) -> dict:
        return self.forge_theme.theme()

    def reload_theme(self) -> None:
        self.forge_theme.apply(self.hub)

    def actions(self) -> list[str]:
        return ["edit"]  # particles.py quits when its switches are off

    def action(self, name: str) -> dict:
        if name != "edit":
            return {"error": f"no action called {name}"}
        if core.particles.ask_to_edit() != 0:
            return {"error": "Forge Desktop is not running."}
        return {"ok": True}


def linux_settings(window: Callable[[str], int] | None = None, theme=None) -> int:
    theme = theme or load_theme("linux")
    paths = core.Paths(Path(theme.CONFIG).parent)
    desk = LinuxSettings(NullShell(), registry.load(), "linux", paths, theme, {})
    server = Server(ROOT, desk).start()
    desk.urls = server.url
    try:
        return (window or gtk_window)(server.url("desktop/settings.html"))
    finally:
        server.stop()


def gtk_window(url: str) -> int:
    """The page in a plain GTK window: particles.py's own toolkit, so the
    settings need nothing the desktop does not."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2  # type: ignore  # GObject introspection
    except (ImportError, ValueError) as e:
        print(
            f"The settings window needs GTK 3 and WebKit2GTK 4.1: {e}", file=sys.stderr
        )
        return 2
    win = Gtk.Window(title=f"{APP_NAME} settings")
    win.set_default_size(560, 760)
    view = WebKit2.WebView()
    view.load_uri(url)
    win.add(view)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    win.show_all()
    Gtk.main()
    return 0


def make_shell(
    os_name: str, webview, log, w32, cut: dict, preview: bool
) -> WebviewShell:
    if preview:
        return PreviewShell(webview, None, log)
    if os_name == "windows":
        from . import win_desktop, windows

        layout = win_desktop.wait_for_layout(w32)
        log(f"behind the desktop icons: {layout.kind} {layout.why}".rstrip())
        if layout.kind == "none":
            cut["scenery"] = layout.why
        windows.quiet_navigation()
        command = win_desktop.login_command(
            Path(sys.executable), ROOT / "desktop" / "start.py"
        )
        return windows.WindowsShell(webview, None, log, w32, layout, command)
    from . import macos

    return macos.MacShell(webview, None, log, ROOT / "desktop" / "start.py")


def parse(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="forge-desktop", description="Forge Desktop")
    ask = p.add_mutually_exclusive_group()
    for name in ("settings", "edit", "quit"):
        ask.add_argument(f"--{name}", dest="ask", action="store_const", const=name)
    p.add_argument("--selftest", action="store_true", help="what works here, as JSON")
    p.add_argument(
        "--preview", action="store_true", help="the page in an ordinary window"
    )
    args, extra = p.parse_known_args(argv)
    # Finder may add -psn_0_123 (a process serial number) on a first start
    unknown = [a for a in extra if not a.startswith("-psn_")]
    if unknown:
        p.error("unrecognized arguments: " + " ".join(unknown))
    return args


def main(argv=None) -> int:
    args = parse(argv)
    os_name = registry.this_os()
    if args.selftest:
        return selftest(os_name)
    if os_name == "linux" and not args.preview:
        if args.ask == "settings":
            return linux_settings()
        if args.ask == "edit":
            return core.particles.ask_to_edit()
        print(
            "On Linux Forge Desktop runs through particles.py (install.sh sets it up); "
            "--settings opens its settings, --preview shows the page in an ordinary window.",
            file=sys.stderr,
        )
        return 2
    paths = core.Paths(config_dir(os_name))
    log = open_log(paths)
    try:
        return run(args, os_name, paths, log)
    except Exception:
        log("could not start:\n" + traceback.format_exc())
        tell_user(
            os_name,
            f"Forge Desktop could not start. What went wrong is in {paths.config}.",
        )
        return 1


def run(args, os_name: str, paths: core.Paths, log) -> int:
    w32 = None
    if os_name == "windows":  # the preview too: the mutex, not fcntl
        from . import win_desktop

        win_desktop.Win32.dpi_aware()
        w32 = win_desktop.Win32()
    if not first_instance(paths, w32):
        if w32 is not None:
            w32.allow_foreground()  # so the running one may show its settings
        pass_on(paths, args.ask or "settings")
        if args.ask == "quit" and not wait_for_exit(paths, w32):
            log("a quit was passed on, but the running copy did not end")
            return STILL_RUNNING
        return 0
    if args.ask == "quit":
        return 0  # nothing running to quit
    if args.ask == "edit":
        pass_on(paths, "edit")  # read once the pages are up
    point_theme(paths)
    theme = load_theme(os_name)
    reg = registry.load()
    sources, cut = pick_sources(reg, os_name, anywhere=args.preview)
    import webview  # here: --selftest and the tests never need it

    if os_name == "windows" and not webview2_ready():
        log("no WebView2 runtime")
        tell_user(os_name, NO_WEBVIEW2)
        return 1
    shell = make_shell(os_name, webview, log, w32, cut, args.preview)
    desk = core.Core(shell, reg, os_name, paths, theme, sources, cut)
    server = Server(ROOT, desk).start()
    shell.core, shell.server, desk.urls = desk, server, server.url
    shell.after_quit = lambda: exit_soon(log)
    first = desk.first_run()
    if not args.preview:
        keep_login(desk, first, log)
    log(
        f"started on {os_name}; providers {sorted(k for k, v in sources.items() if v)}; cut {cut}"
    )
    keeper = webview.create_window(
        APP_NAME, html=KEEPER_HTML, hidden=True, width=200, height=100, focus=False
    )
    threading.Thread(target=desk.run_timers, daemon=True).start()

    def started():
        shell.started(keeper)
        if first or args.ask == "settings" or args.preview:
            shell.open_settings()

    webview.start(
        started,
        private_mode=False,
        storage_path=str(webview_storage(os_name, paths)),
    )
    desk.stop()
    server.stop()
    log("stopped")
    return 0


# --- the self-test (the Windows build runs it under Wine) ---------------------------
def selftest(os_name: str, out: Callable[[str], Any] = print) -> int:
    report: dict[str, Any] = {
        "os": os_name,
        "python": sys.version.split()[0],
        "problems": [],
    }
    problems = report["problems"]
    with tempfile.TemporaryDirectory() as tmp:
        paths = core.Paths(Path(tmp))
        point_theme(paths)
        theme = load_theme(os_name)
        reg = registry.load()
        # on Linux (a check while building) whatever works here
        sources, cut = pick_sources(reg, os_name, anywhere=os_name == "linux")
        report["providers"] = {
            k: (v.__name__.rsplit(".", 1)[-1] if v else None)
            for k, v in sources.items()
        }
        if os_name == "windows":
            cut.update(_windows_check(report))
        report["cut"] = cut
        desk = core.Core(NullShell(), reg, os_name, paths, theme, sources, cut)
        report["first_run"] = desk.first_run()
        report["wanted"] = desk.wanted()
        if sources.get("stats") is not None:
            try:
                report["stats"] = sorted(sources["stats"].Stats().read())
            except Exception as e:
                problems.append(f"stats: {e!r}")
        server = Server(ROOT, desk).start()
        desk.urls = server.url
        try:
            page = _get(server.url("desktop/particles.html?screen=0&part=widgets"))
            if "FORGE_DESKTOP" not in page or "forge_desktop.js" not in page:
                problems.append("the desktop page came without its boot data")
            info = json.loads(_get(server.url("api/settings")))
            report["settings_groups"] = [g["title"] for g in info["groups"]]
            if not info["groups"]:
                problems.append("no settings rows")
            if "<html" not in _get(server.url("desktop/settings.html")).lower():
                problems.append("no settings page")
            if "FORGE_THEME" not in _get(server.url("hub/theme.js")):
                problems.append("no theme script")
        except Exception as e:
            problems.append(f"server: {e!r}")
        finally:
            server.stop()
    try:
        import importlib.metadata

        import webview  # noqa: F401

        report["webview"] = importlib.metadata.version("pywebview")
    except Exception as e:
        report["webview"] = None
        problems.append(f"webview: {e!r}")
    report["ok"] = not problems
    out(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


def _windows_check(report: dict) -> dict:
    """Windows' desktop tree and screens, as far as this machine (or Wine) has them."""
    from . import win_desktop

    try:
        win_desktop.Win32.dpi_aware()
        w32 = win_desktop.Win32()
        layout = win_desktop.find_layout(w32)
        report["behind_icons"] = layout.kind
        report["monitors"] = len(w32.monitors())
        try:
            # pywebview's WinForms needs .NET through these
            for name in ("clr_loader", "pythonnet"):
                importlib.import_module(name)
            report["pythonnet"] = "imports"
        except Exception as e:
            report["pythonnet"] = repr(e)
        return {"scenery": layout.why} if layout.kind == "none" else {}
    except Exception as e:
        report["behind_icons"] = f"error: {e!r}"
        return {}


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode("utf-8")
