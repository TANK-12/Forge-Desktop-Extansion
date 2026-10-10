"""Forge Desktop on Windows: the desktop's window tree and the rest of the
Win32 side, kept apart from pywebview so the decisions can be tested with a
fake window tree on Linux (Relay task 130, 2026-10-09).

Behind the desktop icons. Live-wallpaper apps (Lively, kirie, rexxpaper)
put their window there in one of two ways:

- classic (Windows 10, Windows 11 before 24H2): message 0x052C to Progman
  makes Explorer split the desktop into a WorkerW holding the icons
  (SHELLDLL_DefView) and an empty WorkerW behind it; ours goes into that one.
- raised (Windows 11 24H2 and later): Progman has WS_EX_NOREDIRECTIONBITMAP
  and keeps the icons and the WorkerW as its own children. After 0x052C
  with (0xD, 1) ours becomes a layered child of Progman, directly below
  SHELLDLL_DefView, with Explorer's WorkerW moved below ours.

No layout found (another shell, a future Windows): the scenery is cut on
that machine, with the reason in Settings; the widgets still work.

Above the icons the widgets window sits at the bottom of the window stack,
except while the desktop is in front of everything (Win+D): then it goes
just above the desktop until something else is in front again. A marker
window that stays at the bottom tells the two apart, the way Rainmeter's
helper window does.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SPAWN_WORKERW = 0x052C
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
GWL_STYLE = -16
GWL_EXSTYLE = -20
HWND_TOP = 0
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
LWA_ALPHA = 0x2
# windows of the shell itself: never "an app covering the screen"
SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow",
    "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland",
}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Forge Desktop"
WALLPAPER_STYLES = {"10": "fill", "6": "fit", "2": "stretch", "22": "span"}


@dataclass
class Layout:
    kind: str  # classic, raised or none
    progman: int = 0
    defview: int = 0
    workerw: int = 0
    why: str = ""


def find_layout(w) -> Layout:
    """Where the scenery can go behind the desktop icons on this machine."""
    progman = w.find_window("Progman")
    if not progman:
        return Layout(
            "none", why="Windows' desktop (Progman) was not found; is Explorer running?"
        )
    if w.ex_style(progman) & WS_EX_NOREDIRECTIONBITMAP:
        w.send_timeout(progman, SPAWN_WORKERW, 0xD, 1)
        found = _raised(w, progman)
        if found:
            return found
    w.send_timeout(progman, SPAWN_WORKERW, 0, 0)
    for hwnd in w.top_windows():
        if w.find_child(hwnd, 0, "SHELLDLL_DefView"):
            workerw = w.find_child(0, hwnd, "WorkerW")
            if workerw:
                defview = w.find_child(hwnd, 0, "SHELLDLL_DefView")
                return Layout("classic", progman, defview, workerw)
    found = _raised(w, progman)
    if found:
        return found
    return Layout(
        "none",
        progman,
        why="no place behind the desktop icons was found (this Windows lays its desktop out differently)",
    )


def wait_for_layout(w, tries: int = 20, pause: float = 1.0, sleep=time.sleep) -> Layout:
    """find_layout, giving Explorer up to tries x pause seconds to make its
    desktop: at log-in Forge Desktop can start before Explorer has."""
    layout = find_layout(w)
    for _ in range(tries):
        if layout.progman:
            break
        sleep(pause)
        layout = find_layout(w)
    return layout


def _raised(w, progman: int) -> Layout | None:
    defview = w.find_child(progman, 0, "SHELLDLL_DefView")
    workerw = w.find_child(progman, 0, "WorkerW")
    if defview and workerw:
        return Layout("raised", progman, defview, workerw)
    return None


def place_scenery(
    w, hwnd: int, layout: Layout, x: int, y: int, width: int, height: int
) -> bool:
    """Put the scenery window behind the desktop icons over the screen at
    (x, y, width, height) in screen pixels. False when there is no place."""
    if layout.kind == "classic":
        parent = layout.workerw
        if w.get_long(hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
            # pywebview's hidden start leaves the window layered with no
            # alpha set, and such a window draws nothing
            w.layered_alpha(hwnd, 255)
    elif layout.kind == "raised":
        parent = layout.progman
        w.set_long(
            hwnd, GWL_STYLE, (w.get_long(hwnd, GWL_STYLE) & ~WS_POPUP) | WS_CHILD
        )
        w.set_long(hwnd, GWL_EXSTYLE, w.get_long(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED)
        w.layered_alpha(hwnd, 255)
    else:
        return False
    w.set_parent(hwnd, parent)
    ox, oy = w.client_origin(parent)  # a child is placed in its parent's terms
    if layout.kind == "raised":
        w.set_pos(hwnd, layout.defview, x - ox, y - oy, width, height, SWP_NOACTIVATE)
        # Explorer's wallpaper below every scenery window (one per screen)
        w.set_pos(
            layout.workerw,
            HWND_BOTTOM,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    else:
        w.set_pos(hwnd, 0, x - ox, y - oy, width, height, SWP_NOZORDER | SWP_NOACTIVATE)
    w.show(hwnd, SW_SHOWNOACTIVATE)
    return True


def close_reason(args) -> str:
    """The name of a WinForms FormClosingEventArgs' CloseReason
    ("UserClosing", "WindowsShutDown", "TaskManagerClosing", ...)."""
    reason = getattr(args, "CloseReason", None)
    try:
        name = str(reason.ToString())  # type: ignore[union-attr]
    except Exception:
        name = str(reason)
    return name.rsplit(".", 1)[-1]


def holder(w) -> int:
    """The top-level window that holds the desktop icons."""
    for hwnd in w.top_windows():
        if w.find_child(hwnd, 0, "SHELLDLL_DefView"):
            return hwnd
    return 0


def desktop_in_front(order: list[int], desktop: int, marker: int) -> bool:
    """Win+D: the window holding the icons is above the marker, which is
    kept at the bottom of the stack. order: top-level windows, top first."""
    if not desktop or not marker or desktop not in order or marker not in order:
        return False
    return order.index(desktop) < order.index(marker)


def is_app_window(
    cls: str, ex_style: int, visible: bool, iconic: bool, cloaked: bool, ours: bool
) -> bool:
    """An app's own window that can cover a screen: shown, not minimised, on
    this virtual desktop (not cloaked), not the shell's, not ours, and not a
    tool window or a click-through overlay (game bars, recorders)."""
    if not visible or iconic or cloaked or ours or cls in SHELL_CLASSES:
        return False
    return not ex_style & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)


def monitor_of(rect, monitors) -> int | None:
    """The monitor a window is mostly on; rects are (left, top, right, bottom)."""
    best, best_area = None, 0
    for n, m in enumerate(monitors):
        w = min(rect[2], m[2]) - max(rect[0], m[0])
        h = min(rect[3], m[3]) - max(rect[1], m[1])
        if w > 0 and h > 0 and w * h > best_area:
            best, best_area = n, w * h
    return best


def covered_screens(windows: list[dict], monitors) -> set[int]:
    """Screens an app window hides: a maximised one, or one as big as the
    screen (full screen). windows: {"rect": (l, t, r, b), "zoomed": bool}."""
    out = set()
    for win in windows:
        n = monitor_of(win["rect"], monitors)
        if n is None:
            continue
        r, m = win["rect"], monitors[n]
        full = r[0] <= m[0] and r[1] <= m[1] and r[2] >= m[2] and r[3] >= m[3]
        if win.get("zoomed") or full:
            out.add(n)
    return out


def wallpaper_style(style: str, tile: str) -> str:
    """HKCU\\Control Panel\\Desktop WallpaperStyle + TileWallpaper as the
    page's names (forge_desktop.js wallpaper())."""
    style, tile = str(style or "").strip(), str(tile or "").strip()
    if style in WALLPAPER_STYLES:
        return WALLPAPER_STYLES[style]
    if style in ("0", ""):
        return "tile" if tile == "1" else "center"
    return "fill"


def colour(text: str) -> str:
    """HKCU\\Control Panel\\Colors Background ("r g b") as #rrggbb."""
    try:
        parts = [int(p) for p in str(text).split()]
    except ValueError:
        return ""
    if len(parts) != 3 or any(not 0 <= p <= 255 for p in parts):
        return ""
    return "#" + "".join(f"{p:02x}" for p in parts)


def login_command(python: Path, app: Path) -> str:
    """The Run key's command: the windowless Python beside the app."""
    pythonw = (
        python.with_name("pythonw.exe")
        if python.name.lower() == "python.exe"
        else python
    )
    return f'"{pythonw}" "{app}"'


def login_state(reg) -> bool:
    """Whether Windows starts Forge Desktop at log-in (HKCU Run key; no admin)."""
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = reg.QueryValueEx(key, RUN_NAME)
    except OSError:
        return False
    return bool(str(value).strip())


def set_login(reg, on: bool, command: str) -> None:
    with reg.CreateKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            reg.SetValueEx(key, RUN_NAME, 0, reg.REG_SZ, command)
        else:
            try:
                reg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass


@dataclass
class Wallpaper:
    path: str = ""
    style: str = "fill"
    color: str = ""
    stamp: str = ""
    extra: dict = field(default_factory=dict)


def read_wallpaper(w, reg) -> Wallpaper:
    """The picture Windows shows, how it lays it out, and the colour around it."""
    path = w.wallpaper_path()
    values = {}
    for sub, name in (
        (r"Control Panel\Desktop", "WallpaperStyle"),
        (r"Control Panel\Desktop", "TileWallpaper"),
        (r"Control Panel\Colors", "Background"),
    ):
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, sub) as key:
                values[name] = str(reg.QueryValueEx(key, name)[0])
        except OSError:
            values[name] = ""
    stamp = ""
    if path:
        try:
            info = os.stat(path)
            stamp = f"{info.st_mtime_ns}-{info.st_size}"
        except OSError:
            path = ""
    return Wallpaper(
        path,
        wallpaper_style(values["WallpaperStyle"], values["TileWallpaper"]),
        colour(values["Background"]),
        stamp,
    )


class Win32:
    """The real Win32 calls (ctypes); only made on Windows."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        dll = getattr(ctypes, "WinDLL")  # Windows only
        self.ctypes, self.wt = ctypes, wintypes
        self.u = dll("user32", use_last_error=True)
        self.gdi = dll("gdi32")
        self.dwm = dll("dwmapi")
        self.k = dll("kernel32", use_last_error=True)
        try:
            self.shcore = dll("shcore")
        except OSError:  # before Windows 8.1: one DPI for every screen
            self.shcore = None
        H, I, P = wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t
        for name, res, args in (
            ("FindWindowW", H, [wintypes.LPCWSTR, wintypes.LPCWSTR]),
            ("FindWindowExW", H, [H, H, wintypes.LPCWSTR, wintypes.LPCWSTR]),
            ("SetParent", H, [H, H]),
            ("GetForegroundWindow", H, []),
            ("GetWindowLongPtrW", P, [H, I]),
            ("SetWindowLongPtrW", P, [H, I, P]),
            ("SetWindowPos", wintypes.BOOL, [H, H, I, I, I, I, wintypes.UINT]),
            ("ShowWindow", wintypes.BOOL, [H, I]),
            ("IsWindowVisible", wintypes.BOOL, [H]),
            ("IsIconic", wintypes.BOOL, [H]),
            ("IsZoomed", wintypes.BOOL, [H]),
            ("GetClassNameW", I, [H, wintypes.LPWSTR, I]),
            ("GetWindowRect", wintypes.BOOL, [H, ctypes.POINTER(wintypes.RECT)]),
            ("ClientToScreen", wintypes.BOOL, [H, ctypes.POINTER(wintypes.POINT)]),
            (
                "SetLayeredWindowAttributes",
                wintypes.BOOL,
                [H, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD],
            ),
            ("SetWindowRgn", I, [H, wintypes.HANDLE, wintypes.BOOL]),
            (
                "GetWindowThreadProcessId",
                wintypes.DWORD,
                [H, ctypes.POINTER(wintypes.DWORD)],
            ),
            ("GetDpiForWindow", wintypes.UINT, [H]),
        ):
            fn = getattr(self.u, name, None)
            if fn is not None:
                fn.restype, fn.argtypes = res, args
        self.u.SendMessageTimeoutW.argtypes = [
            H,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.gdi.CreateRectRgn.restype = wintypes.HANDLE
        self.gdi.CreateRectRgn.argtypes = [I, I, I, I]
        self.gdi.CombineRgn.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            I,
        ]
        self.gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        self.pid = os.getpid()

    @staticmethod
    def dpi_aware() -> None:
        """Per-monitor DPI awareness before any window or monitor is read,
        so every number is in real pixels on every screen."""
        import ctypes

        user32 = getattr(ctypes, "windll").user32  # Windows only
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4))
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    def find_window(self, cls: str) -> int:
        return self.u.FindWindowW(cls, None) or 0

    def find_child(self, parent: int, after: int, cls: str) -> int:
        return self.u.FindWindowExW(parent or None, after or None, cls, None) or 0

    def get_long(self, hwnd: int, index: int) -> int:
        return (
            int(self.u.GetWindowLongPtrW(hwnd, index)) & 0xFFFFFFFF
        )  # 64-bit Python only

    def set_long(self, hwnd: int, index: int, value: int) -> None:
        if value & 0x80000000:  # the sign bit of a 32-bit style
            value -= 1 << 32
        self.u.SetWindowLongPtrW(hwnd, index, value)

    def ex_style(self, hwnd: int) -> int:
        return self.get_long(hwnd, GWL_EXSTYLE)

    def send_timeout(self, hwnd: int, msg: int, wp: int, lp: int) -> None:
        result = self.ctypes.c_size_t(0)
        self.u.SendMessageTimeoutW(
            hwnd, msg, wp, lp, 0, 1000, self.ctypes.byref(result)
        )

    def top_windows(self) -> list[int]:
        out: list[int] = []
        proc = self.ctypes.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)

        def each(hwnd, _):
            out.append(int(hwnd or 0))
            return True

        self.u.EnumWindows(proc(each), 0)
        return out

    def set_parent(self, child: int, parent: int) -> None:
        self.u.SetParent(child, parent)

    def client_origin(self, hwnd: int) -> tuple[int, int]:
        pt = self.wt.POINT(0, 0)
        self.u.ClientToScreen(hwnd, self.ctypes.byref(pt))
        return pt.x, pt.y

    def set_pos(
        self, hwnd: int, after: int, x: int, y: int, w: int, h: int, flags: int
    ) -> None:
        self.u.SetWindowPos(hwnd, after, int(x), int(y), int(w), int(h), flags)

    def layered_alpha(self, hwnd: int, alpha: int) -> None:
        self.u.SetLayeredWindowAttributes(
            hwnd, 0, max(0, min(255, int(alpha))), LWA_ALPHA
        )

    def show(self, hwnd: int, cmd: int) -> None:
        self.u.ShowWindow(hwnd, cmd)

    def class_name(self, hwnd: int) -> str:
        buf = self.ctypes.create_unicode_buffer(256)
        self.u.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        r = self.wt.RECT()
        self.u.GetWindowRect(hwnd, self.ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def cloaked(self, hwnd: int) -> bool:
        value = self.wt.DWORD(0)
        try:
            self.dwm.DwmGetWindowAttribute(
                self.wt.HWND(hwnd),
                14,
                self.ctypes.byref(value),
                self.ctypes.sizeof(value),
            )
        except OSError:
            return False
        return bool(value.value)

    def ours(self, hwnd: int) -> bool:
        pid = self.wt.DWORD(0)
        self.u.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return pid.value == self.pid

    def app_windows(self) -> list[dict]:
        out = []
        for hwnd in self.top_windows():
            cls = self.class_name(hwnd)
            if is_app_window(
                cls,
                self.ex_style(hwnd),
                bool(self.u.IsWindowVisible(hwnd)),
                bool(self.u.IsIconic(hwnd)),
                self.cloaked(hwnd),
                self.ours(hwnd),
            ):
                out.append(
                    {"rect": self.rect(hwnd), "zoomed": bool(self.u.IsZoomed(hwnd))}
                )
        return out

    def foreground(self) -> int:
        return int(self.u.GetForegroundWindow() or 0)

    def is_window(self, hwnd: int) -> bool:
        return bool(self.u.IsWindow(hwnd))

    def dpi(self, hwnd: int) -> int:
        try:
            return int(self.u.GetDpiForWindow(hwnd)) or 96
        except Exception:
            return 96

    def set_region(self, hwnd: int, rects) -> None:
        """Clip a window to rectangles in its own pixels: outside them nothing
        is drawn and clicks reach the window below (hypr-for-windows
        set_region; an empty list makes the whole window click-through)."""
        rgn = self.gdi.CreateRectRgn(0, 0, 0, 0)
        for x, y, w, h in rects:
            part = self.gdi.CreateRectRgn(int(x), int(y), int(x + w), int(y + h))
            self.gdi.CombineRgn(rgn, rgn, part, 2)  # RGN_OR
            self.gdi.DeleteObject(part)
        self.u.SetWindowRgn(hwnd, rgn, True)  # the system owns the region now

    def see_through(self, hwnd: int) -> int:
        """Per-pixel transparency for a WebView2 form: DWM blur-behind with
        an empty region (hypr-for-windows see_through, the way Tauri's tao
        does it); the form itself is painted black by the caller. Answers
        DWM's HRESULT (negative: it failed)."""
        wt = self.wt

        class BlurBehind(self.ctypes.Structure):
            _fields_ = [
                ("dwFlags", wt.DWORD),
                ("fEnable", wt.BOOL),
                ("hRgnBlur", wt.HANDLE),
                ("fTransitionOnMaximized", wt.BOOL),
            ]

        rgn = self.gdi.CreateRectRgn(0, 0, -1, -1)
        bb = BlurBehind(0x1 | 0x2, True, rgn, False)
        hr = self.dwm.DwmEnableBlurBehindWindow(wt.HWND(hwnd), self.ctypes.byref(bb))
        self.gdi.DeleteObject(rgn)
        ex = self.ex_style(hwnd)
        if ex & WS_EX_LAYERED:  # layered with no attributes draws nothing
            self.set_long(hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
        return int(hr)

    def tool_window(self, hwnd: int) -> None:
        """No taskbar button and no Alt+Tab entry."""
        self.set_long(hwnd, GWL_EXSTYLE, self.ex_style(hwnd) | WS_EX_TOOLWINDOW)

    def monitors(
        self,
    ) -> list[tuple[int, int, int, int, bool, float, tuple[int, int, int, int]]]:
        """(left, top, right, bottom, primary, scale, work) of every monitor,
        primary first; scale is the monitor's DPI over 96, work the part
        apps get (less the taskbar) as (x, y, w, h)."""
        ctypes, wt = self.ctypes, self.wt

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD),
            ]

        found = []
        proc = ctypes.WINFUNCTYPE(
            wt.BOOL, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
        )

        def each(mon, hdc, rect, _):
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            self.u.GetMonitorInfoW(mon, ctypes.byref(info))
            r, a = info.rcMonitor, info.rcWork
            dx, dy = wt.UINT(96), wt.UINT(96)
            if self.shcore is not None:
                self.shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(dy))
            scale = (dx.value or 96) / 96
            work = (a.left, a.top, a.right - a.left, a.bottom - a.top)
            primary = bool(info.dwFlags & 1)
            found.append((r.left, r.top, r.right, r.bottom, primary, scale, work))
            return True

        self.u.EnumDisplayMonitors(None, None, proc(each), 0)
        return sorted(found, key=lambda m: (not m[4], m[0], m[1]))

    def wallpaper_path(self) -> str:
        buf = self.ctypes.create_unicode_buffer(1024)
        self.u.SystemParametersInfoW(0x0073, 1024, buf, 0)  # SPI_GETDESKWALLPAPER
        return buf.value

    def allow_foreground(self) -> None:
        """Let the running Forge Desktop bring its settings to the front: a
        process in the background may not take the focus by itself."""
        self.u.AllowSetForegroundWindow(-1)  # ASFW_ANY

    def single_instance(self, name: str = "Local\\ForgeDesktop") -> bool:
        """True for the first Forge Desktop of this user; the mutex lives as
        long as the process. A later start lets go of its handle at once:
        held, it would keep the mutex alive after the first one ended, and
        a quit waiting for that would wait for itself."""
        self.k.CreateMutexW.restype = self.wt.HANDLE
        handle = self.k.CreateMutexW(None, False, name)
        if self.ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.k.CloseHandle.argtypes = [self.wt.HANDLE]
            self.k.CloseHandle(handle)
            return False
        self._mutex = handle
        return True


def available() -> bool:
    return sys.platform == "win32"
