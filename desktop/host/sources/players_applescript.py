"""Music and Spotify on a Mac through AppleScript (Relay task 130).

Since macOS 15.4 an app can no longer read the system's "now playing" for
other apps, so only these two are seen, and only while they are already
open: a player that isn't running is never asked (asking would launch it).
The first time, macOS asks whether Forge Desktop may control Music or
Spotify. Same player dicts as Linux's shared/forge_mpris.players().
"""

from __future__ import annotations

import subprocess

import psutil

APPS = {
    # name: (process name, duration unit in seconds, has artwork url)
    "Music": ("Music", 1.0, False),
    "Spotify": ("Spotify", 0.001, True),
}
SEP = "\x1f"
READ = """tell application "{app}"
  set out to (player state as string)
  try
    set t to current track
    set out to out & "{sep}" & (name of t) & "{sep}" & (artist of t) & "{sep}" & (duration of t) & "{sep}" & (player position) & "{sep}" & (sound volume)
    {art}
  end try
  return out
end tell"""
ART = 'set out to out & "{sep}" & (artwork url of t)'
OPS = {
    "toggle": "playpause",
    "play": "play",
    "pause": "pause",
    "next": "next track",
    "prev": "previous track",
    "raise": "activate",
}


def available() -> str | None:
    import shutil

    return None if shutil.which("osascript") else "osascript is missing"


def running(procs=None) -> list[str]:
    """The supported players that are open now."""
    names = {p: a for a, (p, _u, _art) in APPS.items()}
    found = set()
    try:
        for proc in procs if procs is not None else psutil.process_iter(["name"]):
            info = proc if isinstance(proc, dict) else getattr(proc, "info", None) or {}
            if info.get("name") in names:
                found.add(names[info["name"]])
    except Exception:
        pass
    return [a for a in APPS if a in found]


def script(app: str) -> str:
    art = ART.format(sep=SEP) if APPS[app][2] else ""
    return READ.format(app=app, sep=SEP, art=art)


def _number(text: str) -> float:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return 0.0


def parse(app: str, text: str) -> dict | None:
    """osascript's answer as a player, or None when the app said nothing."""
    parts = text.rstrip("\n").split(SEP)
    state = parts[0].strip().lower()
    if not state:
        return None
    unit = APPS[app][1]
    title = parts[1] if len(parts) > 1 else ""
    art = parts[6].strip() if len(parts) > 6 else ""
    return {
        "bus": f"applescript:{app}",
        "name": app,
        "desktop": "",
        "state": state if state in ("playing", "paused") else "stopped",
        "position": max(0.0, _number(parts[4])) if len(parts) > 4 else 0.0,
        "volume": (
            max(0.0, min(1.0, _number(parts[5]) / 100)) if len(parts) > 5 else 1.0
        ),
        "canVolume": True,
        "canRaise": True,
        "canNext": bool(title),
        "canPrev": bool(title),
        "canSeek": bool(title),
        "title": title,
        "artist": parts[2] if len(parts) > 2 else "",
        "artwork": art if art.startswith("https://") else "",
        "duration": round(_number(parts[3]) * unit, 3) if len(parts) > 3 else 0,
    }


def osascript(text: str, run=subprocess.run) -> str:
    try:
        out = run(["osascript", "-e", text], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


def players(run=subprocess.run, procs=None) -> list[dict]:
    found = []
    for app in running(procs):
        item = parse(app, osascript(script(app), run))
        if item:
            found.append(item)
    return found


def command(app: str, op: str, value: float | None) -> str | None:
    """The AppleScript for one control, or None for one it can't do."""
    if op in OPS:
        return f'tell application "{app}" to {OPS[op]}'
    if op == "seek" and value is not None:
        return f'tell application "{app}" to set player position to {max(0.0, float(value)):.2f}'
    if op == "volume" and value is not None:
        level = round(max(0.0, min(1.0, float(value))) * 100)
        return f'tell application "{app}" to set sound volume to {level}'
    return None


def control(
    bus: str, op: str, value: float | None = None, run=subprocess.run, procs=None
) -> bool:
    app = bus.split(":", 1)[1] if bus.startswith("applescript:") else ""
    if app not in APPS or app not in running(procs):
        return False  # never launches a player that is not open
    text = command(app, op, value)
    if text is None:
        return False
    try:
        return (
            run(["osascript", "-e", text], capture_output=True, timeout=3).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
