"""Forge Desktop on macOS: the parts that need no Mac, so they can be tested
anywhere (Relay task 130, 2026-10-09). macos.py does the AppKit side.

One borderless, see-through window per screen sits one level above the
desktop icons, on every Space, out of Mission Control's way and out of
Cmd+` cycling. macOS has no click-through region for a window, so a
timer asks where the mouse is: over a widget's click area the window takes
the mouse, anywhere else clicks fall through to the desktop. Cocoa counts
screen points from the bottom-left of the main screen, the page counts CSS
pixels (one point each) from its top-left.

Start at log-in is a LaunchAgent in ~/Library/LaunchAgents (no admin, read
by launchd at the next log-in). It opens the app by its bundle id, so it
still finds the app after it was moved.

macOS asks an app before it logs out, restarts or shuts down with a 'quit'
Apple event, and opening a running app again sends it a 'reopen' one
instead of starting a second copy.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

DESKTOP_ICON_LEVEL_KEY = 18  # kCGDesktopIconWindowLevelKey
NORMAL_LEVEL = 0  # NSNormalWindowLevel: while editing, over the apps
# NSWindowCollectionBehavior: CanJoinAllSpaces | Stationary | IgnoresCycle
BEHAVIOUR = (1 << 0) | (1 << 4) | (1 << 6)
ACCESSORY = 1  # NSApplicationActivationPolicyAccessory: no Dock icon
BORDERLESS = 0  # NSWindowStyleMaskBorderless: AppKit keeps a titled window under the menu bar
AGENT_LABEL = "local.forge.desktop"  # the app's bundle id (desktop/release/release.json)


def fourcc(code: str) -> int:
    """An Apple event code such as 'quit' as the number AppKit gives."""
    return int.from_bytes(code.encode("ascii"), "big")


QUIT_EVENT = (fourcc("aevt"), fourcc("quit"))
REOPEN_EVENT = (fourcc("aevt"), fourcc("rapp"))


def is_quit_event(event_class, event_id) -> bool:
    return (event_class, event_id) == QUIT_EVENT


def to_page(mx: float, my: float, frame) -> tuple[float, float]:
    """A Cocoa screen point as the page's CSS pixel. frame: the window's
    (x, y, width, height) in points, y counted up from the main screen's bottom."""
    x, y, _, h = frame
    return mx - x, (y + h) - my


def inside(rects, x: float, y: float) -> bool:
    return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)


def wants_mouse(editing: bool, rects, frame, mouse) -> bool:
    """Whether the window should take the mouse now: everywhere while the
    widgets are being edited, else only over a click area."""
    if editing:
        return True
    return inside(rects, *to_page(mouse[0], mouse[1], frame))


def agent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def login_args(start: Path, python: Path) -> list[str]:
    """What launchd runs at log-in: the app, found by its bundle id, when
    Forge Desktop runs from one (so it starts as the app wherever it was
    moved), else Python on start.py."""
    for parent in start.parents:
        if parent.suffix == ".app":
            return ["/usr/bin/open", "-b", AGENT_LABEL]
    return [str(python), str(start)]


def agent_plist(args: list[str]) -> bytes:
    return plistlib.dumps(
        {
            "Label": AGENT_LABEL,
            "ProgramArguments": list(args),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }
    )


def login_state(path: Path) -> bool:
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return isinstance(data, dict) and data.get("Label") == AGENT_LABEL


def set_login(path: Path, on: bool, args: list[str]) -> None:
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(agent_plist(args))
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def available() -> bool:
    return sys.platform == "darwin"
