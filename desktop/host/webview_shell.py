"""What Forge Desktop's Windows and macOS hosts share on top of pywebview
(Relay task 130, 2026-10-09).

A page talks to the host through its Bridge (window.pywebview.api) and the
host talks to a page through its Outbox. Both exist because of how pywebview
works underneath:

- pywebview runs every call from a page on a new thread of its own, so two
  quick messages can be handled in the wrong order. The page numbers its
  messages (forge_desktop.js send()); for the keys that set a state (click
  areas, editing, the sound, the layout) the Bridge drops one older than
  what it has already applied, and it hands messages on one at a time.
- window.run_js waits until the window's GUI thread has run the script, so
  it must never be called from that thread (it would wait for itself), and
  the core's timers must not wait on it either: each page has its own
  Outbox thread. On Windows every window shares one GUI thread, so a page
  that is slow to run its script still delays the others' pushes; the
  Outbox only keeps that from stalling the core. A push with a slot
  (levels, stats, players, theme) replaces one of the same slot still
  waiting, so a busy page gets the newest numbers instead of a growing
  backlog.

WebviewShell is the pywebview half of a host: a window per page, the
settings window, keeping the windows in step with the switches and the
screens, and quitting. The OS half (windows.py, macos.py) says how to make,
place, clip, raise and rest a window there.
"""

from __future__ import annotations

import functools
import json
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from . import core

# messages that set a state: an older one never undoes a newer one
STATE_KEYS = ("rects", "editing", "levels", "save")
OUTBOX_LIMIT = 300
SYNC_SETTLE = 0.25
SETTINGS_SIZE = (560, 760)
BACKGROUND = "#101318"


class Bridge:
    """One page's window.pywebview.api.

    pywebview walks every public attribute of this object to build the
    page's side, so message() is the only public thing here (hypr-for-windows
    found that a public window attribute made it crawl the whole .NET form)."""

    def __init__(self, deliver: Callable[[str, dict], None], key: str):
        self._deliver = deliver
        self._key = key
        self._lock = threading.Lock()
        self._load: Any = None
        self._seen: dict[str, int] = {}

    def message(self, text, seq=None, load=None):
        try:
            msg = json.loads(text) if isinstance(text, str) else None
        except ValueError:
            return None
        if not isinstance(msg, dict):
            return None
        with self._lock:
            if isinstance(seq, int) and not isinstance(seq, bool):
                if load != self._load:  # the page loaded again: count afresh
                    self._load, self._seen = load, {}
                for key in STATE_KEYS:
                    if key not in msg:
                        continue
                    if seq < self._seen.get(key, -1):
                        del msg[key]
                    else:
                        self._seen[key] = seq
            if msg:
                self._deliver(self._key, msg)
        return None


class Outbox:
    """Scripts for one page, run in order on a thread of its own once the
    page has loaded (ready)."""

    def __init__(
        self,
        run: Callable[[str], Any],
        log: Callable[[str], None] | None = None,
        limit: int = OUTBOX_LIMIT,
    ):
        self._run = run
        self._log = log or (lambda text: None)
        self._limit = limit
        self._items: deque[list] = deque()
        self._cv = threading.Condition()
        self._closed = False
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def put(self, script: str, slot: str | None = None) -> None:
        with self._cv:
            if self._closed:
                return
            if slot is not None:
                for item in self._items:
                    if item[0] == slot:
                        item[1] = script
                        return
            if len(self._items) >= self._limit:
                self._items.popleft()
            self._items.append([slot, script])
            self._cv.notify()

    def waiting(self) -> int:
        with self._cv:
            return len(self._items)

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._items.clear()
            self._cv.notify()
        self.ready.set()

    def _loop(self) -> None:
        while True:
            self.ready.wait()
            with self._cv:
                while not self._items and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                _, script = self._items.popleft()
            try:
                self._run(script)
            except Exception as e:  # a closed or reloading window
                self._log(f"run_js: {e!r}")


@dataclass(frozen=True)
class Screen:
    """A screen in the OS's own units (Windows: pixels, macOS: points, y up)
    and how many of those make one CSS pixel of the page. work: the part
    apps get (Windows: the screen less the taskbar) as (x, y, w, h), when
    the OS half knows it."""

    x: int
    y: int
    w: int
    h: int
    scale: float = 1.0
    work: tuple[int, int, int, int] | None = None

    @property
    def css(self) -> tuple[int, int]:
        s = self.scale or 1.0
        return round(self.w / s), round(self.h / s)

    def for_part(self, part: str) -> "Screen":
        """Where a window of `part` goes: the widgets keep clear of the
        taskbar (their corner buttons sit at the bottom right), everything
        else fills the screen. No work area in the result, so a taskbar
        change reopens only the widgets window."""
        box = self.work if part == "widgets" and self.work else None
        x, y, w, h = box or (self.x, self.y, self.w, self.h)
        return Screen(x, y, w, h, self.scale)


class WebviewShell:
    """core.Shell on pywebview; the OS half fills in the hooks below."""

    split = False  # Windows: the scenery and the widgets in two windows

    def __init__(self, webview, server, log: Callable[[str], None]):
        self.webview = webview
        self.server = server
        self.log = log
        self.core: core.Core | None = None
        self.pages: dict[str, tuple[core.Page, Screen]] = {}
        self.windows: dict[str, Any] = {}
        self.outboxes: dict[str, Outbox] = {}
        self.settings_window: Any = None
        self.settings_lock = threading.Lock()
        self.pages_lock = threading.RLock()
        self.quitting = False
        self.sync_asked = threading.Event()
        self.keeper: Any = None
        # windows the host itself is closing (WinForms asks closing first)
        self.letting_go: set[int] = set()
        # a load can be reported twice (the event, and open_page seeing it
        # had already fired): one on_loaded at a time
        self.loading = threading.Lock()
        # run once everything is asked to close (app.py: end the process soon)
        self.after_quit: Callable[[], Any] | None = None

    # --- hooks for the OS half -------------------------------------------
    def screens(self) -> list[Screen]:
        raise NotImplementedError

    def window_args(self, page: core.Page, screen: Screen) -> dict:
        """create_window's arguments for this page's window."""
        raise NotImplementedError

    def on_loaded(self, page: core.Page, window) -> None:
        """The page has loaded: style, place and show its window."""

    def on_closed(self, page: core.Page, window) -> None:
        """The page's window is going away."""

    def set_rects(self, page: core.Page, rects) -> None:
        raise NotImplementedError

    def present(self, page: core.Page) -> None:
        raise NotImplementedError

    def rest(self, page: core.Page, resting: bool) -> None:
        raise NotImplementedError

    # --- core.Shell ---------------------------------------------------------
    def run_js(self, page: core.Page, script: str, slot: str | None = None) -> None:
        box = self.outboxes.get(page.key)
        if box is not None:
            box.put(script, slot)

    def screens_changed(self) -> None:
        self.sync_asked.set()

    def open_settings(self) -> None:
        threading.Thread(target=self._open_settings, daemon=True).start()

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        threading.Thread(target=self._quit, daemon=True).start()

    # --- the windows ----------------------------------------------------------
    def wanted(self) -> dict[str, tuple[int, str, Screen]]:
        """key -> (screen, part, where) for every window there should be."""
        assert self.core is not None
        screens = self.screens()
        if not screens:
            return {}
        theme = self.core.theme()
        count = core.particles.screen_count(theme, len(screens))
        parts = self.core.parts(self.split)
        return {
            f"{part}{n}": (n, part, screens[n].for_part(part))
            for n in range(min(count, len(screens)))
            for part in parts
        }

    def sync(self) -> None:
        """Open and close windows until they match wanted()."""
        if self.quitting or self.core is None:
            return
        want = self.wanted()
        with self.pages_lock:
            have = {key: where for key, (_, where) in self.pages.items()}
        for key, where in have.items():
            if key not in want or want[key][2] != where:
                self.close_page(key)
        for key, (screen, part, where) in want.items():
            if key not in self.pages:
                self.open_page(key, screen, part, where)

    def open_page(self, key: str, screen: int, part: str, where: Screen) -> None:
        assert self.core is not None
        width, height = where.css
        page = core.Page(key=key, screen=screen, part=part, width=width, height=height)
        box = Outbox(lambda script, k=key: self._run_js(k, script), self.log)
        bridge = Bridge(self.core.on_message, key)
        url = self.server.url(f"desktop/particles.html?screen={screen}&part={part}")
        window = self.webview.create_window(
            f"Forge Desktop {part} {screen}",
            url,
            js_api=bridge,
            **self.window_args(page, where),
        )
        page.window = window
        with self.pages_lock:
            self.pages[key] = (page, where)
            self.windows[key] = window
            self.outboxes[key] = box
        window.events.loaded += lambda: self._loaded(key)
        # a partial: pywebview calls a handler with no parameters as fn()
        window.events.closing += functools.partial(self._closing, window)
        if self.quitting:
            # quit came while this window was being made: _quit has its list
            self.close_page(key)
            return
        self.core.add_page(page)  # may wait while the theme is redrawn
        if window.events.loaded.is_set():
            # it loaded before the handler was there (on_loaded is repeatable)
            self._loaded(key)
        self.log(f"opened {key} at {where}")

    def close_page(self, key: str) -> None:
        with self.pages_lock:
            page, _ = self.pages.pop(key, (None, None))
            window = self.windows.pop(key, None)
            box = self.outboxes.pop(key, None)
        if self.core is not None:
            self.core.remove_page(key)
        if box is not None:
            box.close()
        if window is not None and page is not None:
            try:
                self.on_closed(page, window)
                self.letting_go.add(id(window))
                window.destroy()
            except Exception as e:
                self.log(f"close {key}: {e!r}")

    def _run_js(self, key: str, script: str) -> None:
        window = self.windows.get(key)
        if window is not None:
            window.run_js(script)

    def _loaded(self, key: str) -> None:
        with self.pages_lock:
            page, _ = self.pages.get(key, (None, None))
            window = self.windows.get(key)
            box = self.outboxes.get(key)
        if page is None or window is None or box is None:
            return
        try:
            with self.loading:
                self.on_loaded(page, window)
        except Exception as e:
            self.log(f"loaded {key}: {e!r}")
        box.ready.set()

    def _closing(self, window):
        # Alt+F4 on a desktop window would close it for good: only quitting
        # or the host's own close_page may (a False cancels the close)
        if self.quitting or id(window) in self.letting_go:
            self.letting_go.discard(id(window))
            return None
        return False

    def started(self, keeper) -> None:
        """pywebview's GUI is up (webview.start's func, on its own thread);
        keeper: the hidden window that keeps the app alive with no pages."""
        self.keeper = keeper
        threading.Thread(target=self.run_sync, daemon=True).start()

    def run_sync(self) -> None:
        """The thread that keeps the windows in step: a burst of asks (the
        theme reloads on every switch) becomes one sync."""
        self.sync()
        while not self.quitting:
            self.sync_asked.wait()
            if self.quitting:
                return
            threading.Event().wait(SYNC_SETTLE)
            self.sync_asked.clear()
            try:
                self.sync()
            except Exception as e:
                self.log(f"sync: {e!r}")

    # --- settings and quitting --------------------------------------------------
    def _open_settings(self) -> None:
        with self.settings_lock:
            window = self.settings_window
            if window is not None:
                try:
                    window.restore()
                    window.show()
                    return
                except Exception:
                    self.settings_window = None
            width, height = SETTINGS_SIZE
            window = self.webview.create_window(
                "Forge Desktop settings",
                self.server.url("desktop/settings.html"),
                width=width,
                height=height,
                min_size=(420, 480),
                background_color=BACKGROUND,
                text_select=True,
            )
            window.events.closed += self._settings_closed
            self.settings_window = window

    def _settings_closed(self) -> None:
        with self.settings_lock:
            self.settings_window = None

    def _quit(self) -> None:
        self.log("quitting")
        if self.core is not None:
            self.core.stop()
        self.sync_asked.set()
        for key in list(self.windows):
            self.close_page(key)
        for window in (self.settings_window, self.keeper):
            if window is not None:
                try:
                    window.destroy()
                except Exception as e:
                    self.log(f"quit: {e!r}")
        try:
            self.server.stop()
        except Exception:
            pass
        if self.after_quit is not None:
            self.after_quit()
