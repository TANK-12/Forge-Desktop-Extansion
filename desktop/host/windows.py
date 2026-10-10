"""Forge Desktop's Windows host (Relay task 130, 2026-10-09): pywebview on
WebView2, with the Win32 side in win_desktop.py.

Two windows per screen:
- the scenery (particles, the visualiser, and the wallpaper painted under
  them, since nothing else shows through there): behind the desktop icons,
  never takes input or focus;
- the widgets: a see-through window over the screen, clipped to the page's
  click areas (outside them nothing is drawn and clicks reach the desktop),
  with no taskbar button. It sits just above the desktop, under every app,
  goes above the desktop while the desktop is in front (Win+D), and comes
  forward while the widgets are being edited.

A screen hidden by a maximised or full-screen app rests: its pages' web
views are hidden, so WebView2 stops drawing them.
"""

from __future__ import annotations

import importlib
import threading
import time
from pathlib import Path

from . import core, win_desktop
from .webview_shell import Screen, WebviewShell

PLACE_EVERY = 0.5
LOOK_EVERY = 2.0


def _hwnd(window) -> int:
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        return 0


def _on_form(window, fn) -> None:
    """Run fn on the window's GUI thread (WinForms wants that) and wait."""
    from System import Action  # type: ignore  # pythonnet, Windows only

    form = window.native
    form.Invoke(Action(fn))


class WindowsShell(WebviewShell):
    split = True

    def __init__(
        self,
        webview,
        server,
        log,
        w32,
        layout: win_desktop.Layout,
        command: str,
        reg=None,
    ):
        super().__init__(webview, server, log)
        # the registry (winreg on Windows; a fake in the tests)
        self.reg = reg if reg is not None else importlib.import_module("winreg")
        self.w = w32
        self.layout = layout
        self.command = command
        self.hwnds: dict[str, int] = {}
        self.styled: set[str] = set()
        self.front = False
        self.marker = 0
        self.seen_monitors: list = []

    # --- WebviewShell's hooks ----------------------------------------------
    def screens(self) -> list[Screen]:
        return [
            Screen(left, top, right - left, bottom - top, scale, work)
            for left, top, right, bottom, _, scale, work in self.w.monitors()
        ]

    def window_args(self, page: core.Page, screen: Screen) -> dict:
        widgets = page.part == "widgets"
        return {
            "x": screen.x,
            "y": screen.y,
            "width": page.width,
            "height": page.height,
            "frameless": True,
            "easy_drag": False,
            "shadow": False,
            "resizable": False,
            "hidden": True,  # shown once styled and in place (on_loaded)
            "focus": widgets,  # the notes and alarms take typing
            "transparent": widgets,
            "text_select": widgets,
            "background_color": "#000000",
        }

    def on_loaded(self, page: core.Page, window) -> None:
        hwnd = _hwnd(window)
        if not hwnd:
            return
        self.hwnds[page.key] = hwnd
        where = self.pages[page.key][1]
        first = page.key not in self.styled
        self.styled.add(page.key)
        self.w.tool_window(hwnd)
        if first:
            window.native.FormClosing += self._form_closing
        if page.part == "scenery":
            placed = win_desktop.place_scenery(
                self.w, hwnd, self.layout, where.x, where.y, where.w, where.h
            )
            self.log(f"{page.key}: behind the icons ({self.layout.kind}): {placed}")
            return
        self.w.see_through(hwnd)

        def black():
            from System.Drawing import Color  # type: ignore  # Windows only

            window.native.BackColor = Color.Black

        _on_form(window, black)
        self.set_rects(
            page, core.particles.clean_rects(page.rects, page.width, page.height)
        )
        self.w.set_pos(
            hwnd,
            win_desktop.HWND_BOTTOM,
            where.x,
            where.y,
            where.w,
            where.h,
            win_desktop.SWP_NOACTIVATE,
        )
        self.w.show(hwnd, win_desktop.SW_SHOWNOACTIVATE)
        if first:
            # a click raises the window over every app: put it back at once
            window.native.Activated += lambda *_: self.place_widgets()
        self.place_widgets()

    def _form_closing(self, sender, args) -> None:
        """WinForms asks every window before Windows signs out, restarts or
        shuts down, and when Task Manager ends the app. pywebview's handler
        has just refused (_closing stops Alt+F4); let those through, or the
        widgets would hold up the restart."""
        if win_desktop.close_reason(args) == "UserClosing":
            return
        args.Cancel = False
        if win_desktop.close_reason(args) == "TaskManagerClosing":
            self.quit()

    def on_closed(self, page: core.Page, window) -> None:
        self.hwnds.pop(page.key, None)
        self.styled.discard(page.key)

    def set_rects(self, page: core.Page, rects) -> None:
        hwnd = self.hwnds.get(page.key)
        if not hwnd or page.part == "scenery":
            return
        s = self.w.dpi(hwnd) / 96  # the page counts CSS pixels
        self.w.set_region(
            hwnd,
            [
                (round(x * s), round(y * s), round(w * s), round(h * s))
                for x, y, w, h in rects
            ],
        )

    def present(self, page: core.Page) -> None:
        hwnd = self.hwnds.get(page.key)
        if hwnd:
            self.w.set_pos(hwnd, win_desktop.HWND_TOP, 0, 0, 0, 0, _KEEP)

    def rest(self, page: core.Page, resting: bool) -> None:
        """Hide a resting page's window (the real wallpaper shows behind the
        icons meanwhile) and its web view, so WebView2 stops drawing."""
        window = self.windows.get(page.key)
        hwnd = self.hwnds.get(page.key)
        if window is None or not hwnd:
            return

        def apply():
            if resting:
                window.native.webview.Visible = False
                self.w.show(hwnd, win_desktop.SW_HIDE)
            else:
                window.native.webview.Visible = True
                self.w.show(hwnd, win_desktop.SW_SHOWNOACTIVATE)

        try:
            _on_form(window, apply)
        except Exception as e:
            self.log(f"rest {page.key}: {e!r}")

    # --- start at log-in ------------------------------------------------------
    def login_state(self) -> bool:
        return win_desktop.login_state(self.reg)

    def set_login(self, on: bool) -> None:
        win_desktop.set_login(self.reg, on, self.command)

    # --- the stack: widgets above the desktop, under the apps -------------------
    def widget_pages(self) -> list[tuple[core.Page, int]]:
        with self.pages_lock:
            pages = [p for p, _ in self.pages.values() if p.part == "widgets"]
        return [(p, self.hwnds[p.key]) for p in pages if self.hwnds.get(p.key)]

    def place_widgets(self) -> None:
        holder = win_desktop.holder(self.w)
        order = self.w.top_windows()
        mine = self.widget_pages()
        ours = {h for _, h in mine} | {self.marker}
        fg = self.w.foreground()
        # an app in front means the desktop is not, whatever the stack says
        calm = (
            not fg
            or fg == holder
            or fg in ours
            or self.w.class_name(fg) in win_desktop.SHELL_CLASSES
        )
        self.front = calm and win_desktop.desktop_in_front(order, holder, self.marker)
        # directly under the lowest window above the desktop that isn't ours,
        # so just over the desktop and under every app; with no Explorer
        # desktop at all, simply at the bottom
        target = win_desktop.HWND_BOTTOM
        if not self.front:
            if holder in order:
                target = win_desktop.HWND_TOP
                for hwnd in reversed(order[: order.index(holder)]):
                    if hwnd not in ours:
                        target = hwnd
                        break
            if self.marker:
                self.w.set_pos(self.marker, target, 0, 0, 0, 0, _KEEP)
        for page, hwnd in mine:
            if page.editing:
                continue
            self.w.set_pos(
                hwnd, win_desktop.HWND_TOP if self.front else target, 0, 0, 0, 0, _KEEP
            )

    def look(self) -> None:
        """Every two seconds: screens, Explorer, covered screens, wallpaper."""
        assert self.core is not None
        monitors = self.w.monitors()
        if monitors != self.seen_monitors:
            self.seen_monitors = monitors
            self.screens_changed()
        gone = [
            key
            for key, hwnd in list(self.hwnds.items())
            if key.startswith("scenery") and not self.w.is_window(hwnd)
        ]
        if gone or (
            self.layout.progman and not self.w.is_window(self.layout.progman)
        ):
            # Explorer restarted or made its desktop afresh, and our scenery
            # went with its windows: find the place again and reopen
            self.layout = win_desktop.find_layout(self.w)
            self.log(f"Explorer's desktop is new; behind the icons: {self.layout.kind}")
            for key in [k for k in self.windows if k.startswith("scenery")]:
                self.close_page(key)
            self.screens_changed()
        rects = [m[:4] for m in monitors]
        covered = (
            set()
            if self.front
            else win_desktop.covered_screens(self.w.app_windows(), rects)
        )
        for n in range(len(monitors)):
            self.core.set_covered(n, n in covered)
        wall = win_desktop.read_wallpaper(self.w, self.reg)
        self.core.set_wallpaper(
            Path(wall.path) if wall.path else None, wall.style, wall.color, wall.stamp
        )

    def run_loop(self) -> None:
        last_look = 0.0
        while not self.quitting:
            try:
                self.place_widgets()
                if time.monotonic() - last_look >= LOOK_EVERY:
                    last_look = time.monotonic()
                    self.look()
            except Exception as e:
                self.log(f"loop: {e!r}")
            time.sleep(PLACE_EVERY)

    def started(self, keeper) -> None:
        """pywebview's GUI is up (webview.start's func, its own thread)."""
        self.keeper = keeper
        for _ in range(100):
            self.marker = _hwnd(keeper)
            if self.marker:
                break
            time.sleep(0.05)
        self.seen_monitors = self.w.monitors()
        threading.Thread(target=self.run_sync, daemon=True).start()
        threading.Thread(target=self.run_loop, daemon=True).start()


_KEEP = win_desktop.SWP_NOMOVE | win_desktop.SWP_NOSIZE | win_desktop.SWP_NOACTIVATE


def quiet_navigation() -> None:
    """pywebview Show()s and Activate()s every transparent window when its
    page starts loading (EdgeChrome.on_navigation_start), which would pop the
    widgets window up over the apps and take the focus. Forge Desktop shows
    its windows itself once they are styled (hypr-for-windows does the same
    for its hidden windows)."""
    try:
        from webview.platforms import edgechromium  # type: ignore
    except Exception:
        return
    original = edgechromium.EdgeChrome.on_navigation_start

    def on_navigation_start(self, sender, args):
        title = str(getattr(self.pywebview_window, "title", ""))
        if title.startswith("Forge Desktop widgets"):
            return
        original(self, sender, args)

    edgechromium.EdgeChrome.on_navigation_start = on_navigation_start
