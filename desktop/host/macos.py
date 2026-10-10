"""Forge Desktop's macOS host (Relay task 130, 2026-10-09): pywebview on
WKWebView, with AppKit through pyobjc. The decisions are in mac_desktop.py.

One window per screen does everything (particles, the visualiser and the
widgets): see-through, one level above the desktop icons, on every Space.
A mouse check fifteen times a second lets it take the mouse only over a
widget; while the widgets are edited it comes forward over the apps. The
app has no Dock icon (an accessory app); its settings open from the corner
button on the desktop or by opening Forge Desktop again (a reopen event).
The windows are made borderless: AppKit keeps a titled window below the
menu bar, which would shift every click area.

macOS pauses a web view that is fully covered by itself, so a page here
never needs to rest.

Not run on a real Mac yet (built and checked on Linux): see desktop/README.md.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from . import core, mac_desktop
from .webview_shell import Screen, WebviewShell

MOUSE_EVERY = 1 / 15
LOOK_EVERY = 2.0


def on_main(fn, wait: bool = True):
    """Run fn on the main thread (AppKit wants that); with wait, return
    what it returned (None after 5 s)."""
    from PyObjCTools import AppHelper  # type: ignore  # macOS only

    if threading.current_thread() is threading.main_thread():
        return fn()
    if not wait:
        AppHelper.callAfter(fn)
        return None
    box: list = []
    done = threading.Event()

    def call():
        try:
            box.append(fn())
        finally:
            done.set()

    AppHelper.callAfter(call)
    done.wait(5)
    return box[0] if box else None


class MacShell(WebviewShell):
    split = False

    def __init__(self, webview, server, log, start: Path, home: Path | None = None):
        super().__init__(webview, server, log)
        self.agent = mac_desktop.agent_path(home or Path.home())
        self.login_command = mac_desktop.login_args(start, Path(sys.executable))
        self.rects: dict[str, list] = {}
        self.applied: dict[str, tuple[bool, bool]] = {}  # key -> (editing, mouse)
        self.seen_screens: list[Screen] = []
        self.icon_level = 0
        # key -> the window's real frame (x, y, w, h) as AppKit placed it
        self.frames: dict[str, tuple[float, float, float, float]] = {}
        self.reopen: object = None  # AppKit does not keep the handler alive

    # --- WebviewShell's hooks ----------------------------------------------
    def screens(self) -> list[Screen]:
        def read():
            from AppKit import NSScreen  # type: ignore  # macOS only

            out = []
            for screen in NSScreen.screens() or []:  # the main screen first
                f = screen.frame()
                out.append(
                    Screen(
                        int(f.origin.x),
                        int(f.origin.y),
                        int(f.size.width),
                        int(f.size.height),
                        1.0,
                    )
                )
            return out

        return on_main(read) or []

    def window_args(self, page: core.Page, screen: Screen) -> dict:
        return {
            "width": page.width,
            "height": page.height,
            "frameless": True,
            "easy_drag": False,
            "shadow": False,
            "resizable": False,
            "hidden": True,  # shown once in place (on_loaded)
            "focus": True,  # the notes and alarms take typing
            "transparent": True,
            "text_select": True,
            "background_color": "#000000",
        }

    def on_loaded(self, page: core.Page, window) -> None:
        where = self.pages[page.key][1]
        level = self.desktop_level()

        def place():
            from AppKit import NSMakeRect  # type: ignore  # macOS only

            native = window.native
            native.setStyleMask_(mac_desktop.BORDERLESS)
            native.setLevel_(level)
            native.setCollectionBehavior_(mac_desktop.BEHAVIOUR)
            native.setHasShadow_(False)
            native.setIgnoresMouseEvents_(True)
            native.setFrame_display_(
                NSMakeRect(where.x, where.y, where.w, where.h), True
            )
            f = native.frame()  # where it really went: the mouse check uses it
            self.frames[page.key] = (
                f.origin.x,
                f.origin.y,
                f.size.width,
                f.size.height,
            )
            native.orderFrontRegardless()

        on_main(place)
        self.applied[page.key] = (False, False)
        self.log(f"{page.key}: on the desktop at level {level}")

    def on_closed(self, page: core.Page, window) -> None:
        self.rects.pop(page.key, None)
        self.applied.pop(page.key, None)
        self.frames.pop(page.key, None)

    def _closing(self, window):
        """macOS asks every window before it logs out, restarts or shuts
        down (a 'quit' Apple event, also sent by Activity Monitor or a
        script): let those through, or the desktop would stop the log-out."""
        if self.system_quit():
            return None
        return super()._closing(window)

    @staticmethod
    def system_quit() -> bool:
        try:
            from Foundation import NSAppleEventManager  # type: ignore  # macOS only

            event = NSAppleEventManager.sharedAppleEventManager().currentAppleEvent()
            if event is None:
                return False
            return mac_desktop.is_quit_event(event.eventClass(), event.eventID())
        except Exception:
            return False

    def listen_for_reopen(self) -> None:
        """Opening the app again while it runs (Finder, Launchpad, Spotlight)
        sends this process a reopen event: show the settings. On the main
        thread, after AppKit set up its own handlers."""
        from Foundation import NSAppleEventManager  # type: ignore  # macOS only

        self.reopen = reopen_handler(self.open_settings)
        NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            self.reopen, "handleReopen:withReply:", *mac_desktop.REOPEN_EVENT
        )

    def set_rects(self, page: core.Page, rects) -> None:
        self.rects[page.key] = list(rects)

    def present(self, page: core.Page) -> None:
        """Editing: the mouse check brings the window forward (tick)."""
        self.tick()

    def rest(self, page: core.Page, resting: bool) -> None:
        pass

    # --- start at log-in ------------------------------------------------------
    def login_state(self) -> bool:
        return mac_desktop.login_state(self.agent)

    def set_login(self, on: bool) -> None:
        mac_desktop.set_login(self.agent, on, self.login_command)

    # --- the mouse, the screens and the wallpaper --------------------------------
    def desktop_level(self) -> int:
        if not self.icon_level:
            try:
                from Quartz import CGWindowLevelForKey  # type: ignore  # macOS only

                self.icon_level = (
                    int(CGWindowLevelForKey(mac_desktop.DESKTOP_ICON_LEVEL_KEY)) + 1
                )
            except Exception:
                self.icon_level = (
                    -2147483603 + 1
                )  # kCGDesktopIconWindowLevel on every macOS so far
        return self.icon_level

    def mouse(self) -> tuple[float, float]:
        from AppKit import NSEvent  # type: ignore  # macOS only

        m = NSEvent.mouseLocation()
        return m.x, m.y

    def tick(self) -> None:
        """Take the mouse over a click area or while editing; let it fall
        through everywhere else. Only changes go to the main thread."""
        mouse = self.mouse()
        with self.pages_lock:
            pages = [
                (p, where, self.windows.get(p.key)) for p, where in self.pages.values()
            ]
        for page, where, window in pages:
            if window is None or page.key not in self.applied:
                continue
            frame = self.frames.get(page.key) or (where.x, where.y, where.w, where.h)
            want = (
                page.editing,
                mac_desktop.wants_mouse(
                    page.editing, self.rects.get(page.key, []), frame, mouse
                ),
            )
            if want == self.applied[page.key]:
                continue
            editing_changed = want[0] != self.applied[page.key][0]
            self.applied[page.key] = want
            on_main(
                lambda w=window, s=want, e=editing_changed: self._apply(w, s, e),
                wait=False,
            )

    def _apply(self, window, state: tuple[bool, bool], editing_changed: bool) -> None:
        editing, takes = state
        native = window.native
        native.setIgnoresMouseEvents_(not takes)
        if editing_changed:
            if editing:
                from AppKit import NSApp  # type: ignore  # macOS only

                native.setLevel_(mac_desktop.NORMAL_LEVEL)
                NSApp.activateIgnoringOtherApps_(True)
                native.makeKeyAndOrderFront_(None)
            else:
                native.setLevel_(self.desktop_level())

    def look(self) -> None:
        """Every two seconds: the screens and the main screen's wallpaper (the
        widgets' glass is drawn from it)."""
        screens = self.screens()
        if screens != self.seen_screens:
            self.seen_screens = screens
            self.screens_changed()
        path = on_main(self._wallpaper)
        if self.core is not None and path:
            try:
                info = Path(path).stat()
                stamp = f"{info.st_mtime_ns}-{info.st_size}"
            except OSError:
                return
            self.core.set_wallpaper(Path(path), "fill", "", stamp)

    @staticmethod
    def _wallpaper() -> str:
        from AppKit import NSScreen, NSWorkspace  # type: ignore  # macOS only

        url = NSWorkspace.sharedWorkspace().desktopImageURLForScreen_(
            NSScreen.mainScreen()
        )
        return str(url.path()) if url is not None else ""

    def run_loop(self) -> None:
        last_look = 0.0
        while not self.quitting:
            try:
                self.tick()
                if time.monotonic() - last_look >= LOOK_EVERY:
                    last_look = time.monotonic()
                    self.look()
            except Exception as e:
                self.log(f"loop: {e!r}")
            time.sleep(MOUSE_EVERY)

    def started(self, keeper) -> None:
        def accessory():
            from AppKit import NSApp  # type: ignore  # macOS only

            # pywebview makes a regular app with a Dock icon when imported
            NSApp.setActivationPolicy_(mac_desktop.ACCESSORY)

        on_main(accessory, wait=False)
        on_main(self.listen_for_reopen, wait=False)
        self.seen_screens = self.screens()
        super().started(keeper)
        threading.Thread(target=self.run_loop, daemon=True).start()


def reopen_handler(callback):
    """An object AppKit can call with the reopen Apple event (its class is
    made here, where pyobjc is known to be there)."""
    from Foundation import NSObject  # type: ignore  # macOS only

    class ForgeDesktopReopen(NSObject):
        def handleReopen_withReply_(self, event, reply):
            callback()

    return ForgeDesktopReopen.alloc().init()
