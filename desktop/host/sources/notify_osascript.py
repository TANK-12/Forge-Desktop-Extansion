"""An alarm's or timer's notification on a Mac through osascript (Relay task
130). The text is passed as an argument, never as script."""

from __future__ import annotations

import shutil
import subprocess

SCRIPT = [
    "-e",
    "on run argv",
    "-e",
    'display notification (item 1 of argv) with title "Forge"',
    "-e",
    "end run",
]


def available() -> str | None:
    return None if shutil.which("osascript") else "osascript is missing"


def args(text: str) -> list[str]:
    return ["osascript", *SCRIPT, text[:200]]


def notify(text: str, popen=subprocess.Popen) -> None:
    try:
        popen(args(text), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
