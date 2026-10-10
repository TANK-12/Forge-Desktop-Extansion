"""The OS-neutral heart of Forge Desktop's Windows and macOS hosts (Relay
task 130, 2026-10-09).

It does what desktop/particles.py's run() does with GTK on Linux, without
any windowing: it answers the pages' messages (rects, editing, save, levels,
players, media, notify, settings, ready), pushes the numbers, the players,
the sound and the theme to them, rests and wakes them, and keeps the
settings. The OS part is a Shell (desktop/host/windows.py, macos.py): it
makes the windows and runs a script in a page, sets where a page takes
clicks, brings one forward and hides or shows one. The data comes from the
providers desktop/platforms.json picks for the OS (desktop/host/sources/).

The pure helpers are particles.py's own (imported, not copied), so the two
hosts read layouts, alarms and click areas the same way.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

DESKTOP = Path(__file__).resolve().parents[1]
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

import particles  # noqa: E402  desktop/particles.py: the shared pure helpers

from . import registry  # noqa: E402

STATS_EVERY = 3.0
SLOW_EVERY = 30.0
HOUSE_EVERY = 5.0
POWER_EVERY = 5.0
ASK_EVERY = 1.0
LEVELS_GRACE = particles.LEVELS_GRACE
AWAKE_FOR = 90.0


@dataclass
class Page:
    """One page in one window: a whole screen ("all"), or on Windows the
    "scenery" behind the desktop icons or the "widgets" above them."""

    key: str
    screen: int
    part: str = "all"
    width: int = 1920
    height: int = 1080
    window: Any = None
    rects: list = field(default_factory=list)
    editing: bool = False
    covered: bool = False
    resting: bool = False
    layout: dict | None = None


class Shell(Protocol):
    # slot: a push that replaces an older one of the same slot still waiting
    # (levels, stats, players, theme), so a busy page gets the newest
    def run_js(self, page: Page, script: str, slot: str | None = None) -> None: ...
    def set_rects(self, page: Page, rects: list[tuple[int, int, int, int]]) -> None: ...
    def present(self, page: Page) -> None: ...
    def rest(self, page: Page, resting: bool) -> None: ...
    def open_settings(self) -> None: ...
    def screens_changed(self) -> None: ...
    def quit(self) -> None: ...


@dataclass
class Paths:
    """Where this OS keeps Forge Desktop's own files (never Forge's)."""

    config: Path

    @property
    def settings(self) -> Path:
        return self.config / "settings.json"

    @property
    def layout(self) -> Path:
        return self.config / "desktop-widgets.json"

    @property
    def cache(self) -> Path:
        return self.config / "cache"

    @property
    def wallpaper_state(self) -> Path:
        return self.config / "desktop-wallpaper"

    @property
    def ask(self) -> Path:
        return self.config / "ask"

    @property
    def pid(self) -> Path:
        return self.config / "forge-desktop.pid"


class Core:
    def __init__(
        self,
        shell: Shell,
        reg: dict,
        os_name: str,
        paths: Paths,
        theme_mod,
        sources: dict[str, Any],
        runtime_cut: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.shell = shell
        self.reg = reg
        self.os = os_name
        self.paths = paths
        self.forge_theme = theme_mod
        self.sources = sources  # kind -> provider module (or None when cut)
        self.caps = registry.caps(reg, os_name, runtime_cut)
        self.clock = clock
        self.now = now
        self.pages: dict[str, Page] = {}
        self.lock = threading.RLock()
        self.levels_obj = None
        self.levels_on: set[str] = set()
        self.levels_at = 0.0
        self.levels_off = ""  # the last reason the sound reader gave up
        self.stats_reader = None
        self.awake_until = 0.0
        self.power = {"on_battery": False, "battery": None, "low": False}
        self.theme_cache: dict | None = None
        self.wallpaper_seen = ""
        # how the OS lays its wallpaper out and its colour around it
        self.wall = {"style": "", "color": "", "v": 0}
        self.urls: Callable[[str], str] = lambda rel: rel
        self.stopping = threading.Event()
        self.players_busy = False

    # --- settings and theme -------------------------------------------------
    def first_run(self) -> bool:
        """No settings yet: the registry's first-run switches (particles and
        widgets on), so a new install shows something. True when written."""
        if self.paths.settings.exists():
            return False
        self.paths.config.mkdir(parents=True, exist_ok=True)
        self.forge_theme.save(dict(self.reg.get("first_run", {})), self.paths.settings)
        return True

    def settings(self) -> dict:
        return self.forge_theme.load(self.paths.settings)

    def theme(self, fresh: bool = False) -> dict:
        """forge_theme's theme with this machine's power and share added, as
        particles.py's reload_theme() adds them on Linux."""
        with self.lock:
            if fresh or self.theme_cache is None:
                self.theme_cache = self.forge_theme.theme(
                    self.settings(), self.paths.cache
                )
            out: dict = dict(self.theme_cache or {})
        # the page's own shared/forge_power.js reads these
        out["on_battery"] = bool(self.power["on_battery"])
        out["gpu_level"] = 0
        out["gpu_reason"] = ""
        out["battery"] = self.power["battery"]
        out["shown"] = 1.0
        # a file:// picture can't load in a page served over http
        for key in ("wallpaper", "wallpaper_blur"):
            if str(out.get(key, "")).startswith("file:"):
                out[key] = self.urls("wallpaper")
        return out

    def theme_js(self) -> str:
        return "window.FORGE_THEME = " + json.dumps(self.theme()) + ";\n"

    def wanted(self) -> bool:
        return particles.wanted(self.theme())

    def wallpaper(self) -> Path | None:
        try:
            path = Path(self.paths.wallpaper_state.read_text(encoding="utf-8").strip())
        except OSError:
            return None
        return path if path.is_file() else None

    def set_wallpaper(
        self, path: Path | None, style: str = "", color: str = "", stamp: str = ""
    ) -> bool:
        """The OS's own wallpaper, from the shell; True when it changed.

        stamp: anything that changes when the picture does under the same
        name (Windows keeps a slideshow's picture in one file)."""
        text = str(path or "")
        seen = "|".join((text, style, color, stamp))
        if seen == self.wallpaper_seen:
            return False
        self.wallpaper_seen = seen
        self.wall = {"style": style, "color": color, "v": self.wall["v"] + 1}
        self.paths.config.mkdir(parents=True, exist_ok=True)
        self.paths.wallpaper_state.write_text(text, encoding="utf-8")
        self.reload_theme()
        self.tell({"wallpaper": self.wallpaper_info()}, slot="wallpaper")
        return True

    def wallpaper_info(self) -> dict:
        """What the scenery paints under the particles (forge_desktop.js)."""
        url = (
            self.urls("wallpaper") + f"?v={self.wall['v']}" if self.wallpaper() else ""
        )
        return {"url": url, "style": self.wall["style"], "color": self.wall["color"]}

    def reload_theme(self) -> None:
        theme = self.theme(fresh=True)
        self.shell.screens_changed()
        script = f"window.forgeTheme && window.forgeTheme({json.dumps(theme)});"
        for page in self.page_list():
            self.shell.run_js(page, script, "theme")
            self.show_or_rest(page)

    # --- the settings window's API (desktop/host/server.py) -----------------
    def settings_info(self) -> dict:
        groups = self.reg.get("settings_groups", [])
        names = [g[0] for g in groups]
        rows = self.forge_theme.settings_rows(names, self.settings())
        hidden = set(self.caps["hide_settings"])
        helps = self.caps.get("help", {})  # the release's wording per OS
        out = []
        for group, title, only in groups:
            mine = [
                {**r, "help": helps[r["key"]]} if r["key"] in helps else r
                for r in rows
                if r.get("group") == group
                and (not only or r["key"] in only)
                and r["key"] not in hidden
            ]
            if mine:
                out.append({"group": group, "title": title, "rows": mine})
        return {
            "groups": out,
            "caps": self.caps,
            "login": self.login_state(),
            "actions": self.actions(),
            "os": self.os,
        }

    def actions(self) -> list[str]:
        """The settings window's buttons this host has (api/do)."""
        return ["edit", "quit"]

    def set_setting(self, key: str, value) -> dict:
        if key not in self.forge_theme.SETTINGS or key in self.caps["hide_settings"]:
            return {"error": f"no setting called {key}"}
        saved = self.forge_theme.save({key: value}, self.paths.settings)
        self.reload_theme()
        return {key: saved.get(key)}

    def action(self, name: str) -> dict:
        if name == "edit":
            self.start_editing()
        elif name == "quit":
            self.shell.quit()
        elif name in ("login_on", "login_off"):
            self.set_login(name == "login_on")
            return {"ok": True, "login": self.login_state()}
        else:
            return {"error": f"no action called {name}"}
        return {"ok": True}

    # start at login is the shell's (a Startup shortcut, a LaunchAgent)
    def login_state(self) -> bool | None:
        fn = getattr(self.shell, "login_state", None)
        return fn() if fn else None

    def set_login(self, on: bool) -> None:
        fn = getattr(self.shell, "set_login", None)
        if fn:
            fn(on)

    # --- pages ----------------------------------------------------------------
    def boot(self, screen: int, part: str) -> dict:
        """The page's window.FORGE_DESKTOP (particles.py injects the same)."""
        with self.lock:
            layout = particles.load_layout(
                particles.layout_path(screen, self.paths.layout)
            )
        boot = {
            "layout": layout,
            "host": self.os,
            "screen": screen,
            "part": part,
            "caps": self.caps,
        }
        if part == "scenery":
            boot["wallpaper"] = self.wallpaper_info()
        return boot

    def add_page(self, page: Page) -> Page:
        with self.lock:
            page.layout = particles.load_layout(
                particles.layout_path(page.screen, self.paths.layout)
            )
            self.pages[page.key] = page
        return page

    def remove_page(self, key: str) -> None:
        with self.lock:
            self.pages.pop(key, None)
            self.levels_on.discard(key)

    def page_list(self) -> list[Page]:
        with self.lock:
            return list(self.pages.values())

    def tell(
        self,
        message: dict,
        only: set[str] | None = None,
        screen: int | None = None,
        slot: str | None = None,
    ) -> None:
        script = f"window.forgeDesktop && window.forgeDesktop({json.dumps(message)});"
        for page in self.page_list():
            if only is not None and page.key not in only:
                continue
            if screen is not None and page.screen != screen:
                continue
            self.shell.run_js(page, script, slot)

    def parts(self, split: bool) -> list[str]:
        """The windows each screen needs now: none while all three desktop
        switches are off; one page doing everything ("all", macOS); or with
        split (Windows) the "scenery" behind the desktop icons while
        particles or the visualiser are on and this machine has a place
        for it, and the "widgets" above the icons while widgets are on."""
        theme = self.theme()
        if not particles.wanted(theme):
            return []
        if not split:
            return ["all"]
        out = []
        moving = theme.get("desktop_particles") is True or theme.get(
            "desktop_visualiser", "off"
        ) not in ("off", None, "")
        if moving and "scenery" not in self.caps["cut"]:
            out.append("scenery")
        if theme.get("desktop_widgets") is True:
            out.append("widgets")
        return out

    def on_message(self, key: str, text) -> None:
        """One message from a page, as JSON text (pywebview's bridge)."""
        page = self.pages.get(key)
        if page is None:
            return
        try:
            msg = json.loads(text) if isinstance(text, str) else text
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if "rects" in msg:
            page.rects = msg["rects"] if isinstance(msg["rects"], list) else []
            self.shell.set_rects(
                page, particles.clean_rects(page.rects, page.width, page.height)
            )
        if "editing" in msg:
            page.editing = bool(msg["editing"])
            if page.editing:
                self.shell.present(page)
            self.show_or_rest(page)
        if "save" in msg and isinstance(msg["save"], dict):
            with self.lock:
                if particles.save_layout(
                    msg["save"], particles.layout_path(page.screen, self.paths.layout)
                ):
                    for other in self.pages.values():
                        if other.screen == page.screen:
                            other.layout = msg["save"]
        if isinstance(msg.get("levels"), dict):
            levels = msg["levels"]
            self.levels(key, bool(levels.get("on")), _int(levels.get("fps"), 30))
        if msg.get("players"):
            self.players()
        if isinstance(msg.get("media"), dict):
            self.media(msg["media"])
        if isinstance(msg.get("notify"), str):
            self.notify(msg["notify"])
        if msg.get("settings"):
            self.shell.open_settings()
        if msg.get("ready"):
            self.push_stats()
            self.players()

    # --- the sound -------------------------------------------------------------
    def levels(self, key: str, on: bool, fps: int = 30) -> None:
        source = self.sources.get("levels")
        if source is None:
            return
        with self.lock:
            if on:
                self.levels_on.add(key)
                self.levels_at = self.clock()
                if self.levels_obj is None:
                    self.levels_obj = source.Levels()
                start = True
            else:
                self.levels_on.discard(key)
                start = False
            obj, idle = self.levels_obj, not self.levels_on
        if obj is None:
            return
        if start:
            obj.start(self.levels_push, 32, max(10, min(60, int(fps))))
        elif idle:
            obj.stop()

    def levels_push(self, event: dict) -> None:
        if event.get("type") == "levels":
            with self.lock:
                only = set(self.levels_on)
            self.tell({"levels": event.get("v")}, only=only, slot="levels")
        elif event.get("type") == "levels-off":
            # the page asks again every few seconds: say each reason once
            reason = str(event.get("reason", ""))
            log = getattr(self.shell, "log", None)
            if reason != self.levels_off and callable(log):
                log(f"visualiser has no sound: {reason}")
            self.levels_off = reason

    # --- players ---------------------------------------------------------------
    def players(self) -> None:
        source = self.sources.get("players")
        if source is None or self.players_busy:
            return
        self.players_busy = True

        def work():
            try:
                self.tell({"players": source.players()}, slot="players")
            except Exception:
                self.tell({"players": []}, slot="players")
            finally:
                self.players_busy = False

        threading.Thread(target=work, daemon=True).start()

    def media(self, msg: dict) -> None:
        source = self.sources.get("players")
        if source is None:
            return
        bus, op = str(msg.get("bus") or ""), str(msg.get("op") or "")
        value = msg.get("value")
        value = float(value) if isinstance(value, (int, float)) else None

        def work():
            try:
                source.control(bus, op, value)
                time.sleep(0.3)
                self.tell({"players": source.players()}, slot="players")
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def notify(self, text: str) -> None:
        source = self.sources.get("notify")
        if source is not None:
            try:
                source.notify(text[:200])
            except Exception:
                pass

    # --- numbers, power, rest ---------------------------------------------------
    def push_stats(self) -> None:
        source = self.sources.get("stats")
        if source is None or self.theme().get("desktop_widgets") is not True:
            return
        if self.stats_reader is None:
            self.stats_reader = source.Stats()
        self.tell({"stats": self.stats_reader.read()}, slot="stats")

    def check_power(self) -> None:
        source = self.sources.get("stats")
        if source is None:
            return
        on_battery, pct = source.power()
        try:
            raw = self.settings().get("battery_saver", 20)
            saver = float(20 if raw is None else raw)
        except (TypeError, ValueError):
            saver = 20.0
        low = pct is not None and pct <= saver
        now = {"on_battery": bool(on_battery), "battery": pct, "low": low}
        if (now["on_battery"], now["low"]) != (
            self.power["on_battery"],
            self.power["low"],
        ):
            self.power = now
            self.reload_theme()
        else:
            self.power = now

    def show_or_rest(self, page: Page) -> None:
        rest = particles.resting(self.theme(), page.covered) and not page.editing
        if self.clock() < self.awake_until:
            rest = False
        if rest != page.resting:
            page.resting = rest
            self.shell.rest(page, rest)

    def set_covered(self, screen: int, covered: bool) -> None:
        for page in self.page_list():
            if page.screen == screen and page.covered != covered:
                page.covered = covered
                self.show_or_rest(page)

    def housekeeping(self) -> None:
        with self.lock:
            idle = (
                self.levels_obj is not None
                and self.clock() - self.levels_at > LEVELS_GRACE
            )
            obj = self.levels_obj
            if idle:
                self.levels_on.clear()
        if idle and obj is not None:
            obj.stop()
        due = any(particles.alarm_due(p.layout, self.now()) for p in self.page_list())
        if due:
            self.awake_until = self.clock() + AWAKE_FOR
        elif self.awake_until and self.clock() > self.awake_until:
            self.awake_until = 0.0
        else:
            return
        for page in self.page_list():
            self.show_or_rest(page)

    def start_editing(self) -> None:
        targets = [p for p in self.page_list() if p.screen == 0 and p.part != "scenery"]
        self.tell({"edit": True}, only={p.key for p in targets})

    def check_asks(self) -> None:
        """Another launch (the Start menu, the Dock, --edit) asks through a
        file: settings, edit or quit."""
        try:
            ask = self.paths.ask.read_text(encoding="utf-8").strip()
            self.paths.ask.unlink()
        except OSError:
            return
        if ask == "settings":
            self.shell.open_settings()
        elif ask == "edit":
            self.start_editing()
        elif ask == "quit":
            self.shell.quit()

    # --- the timers -------------------------------------------------------------
    def run_timers(self) -> None:
        """One background thread: stats every 3 s, power and housekeeping
        every 5 s, asks every second; until stop()."""
        last = {"stats": 0.0, "power": 0.0, "house": 0.0}
        while not self.stopping.wait(ASK_EVERY):
            t = self.clock()
            for name, every, fn in (
                ("stats", STATS_EVERY, self.push_stats),
                ("power", POWER_EVERY, self.check_power),
                ("house", HOUSE_EVERY, self.housekeeping),
            ):
                if t - last[name] >= every:
                    last[name] = t
                    try:
                        fn()
                    except Exception:
                        pass
            try:
                self.check_asks()
            except Exception:
                pass

    def stop(self) -> None:
        self.stopping.set()
        if self.levels_obj is not None:
            self.levels_obj.stop()


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
