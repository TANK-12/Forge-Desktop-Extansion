"""Music players on Windows through its media controls (GSMTC, the players
Windows lists in its volume pop-up), read with pywinrt (Relay task 130).

The same player dicts as Linux's shared/forge_mpris.players(). What Windows'
media controls can't do is reported per player and hidden by the widget:
no volume per player (canVolume false) and no "go to the player"
(canRaise false). No album art for now (the widget shows its note).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

PLAYING, PAUSED = 4, 5
NAMES = {
    "spotify": "Spotify",
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "308046b0af4a39cb": "Firefox",
    "vlc": "VLC",
    "zunemusic": "Media Player",
    "zunevideo": "Films & TV",
    "brave": "Brave",
    "opera": "Opera",
    "foobar2000": "foobar2000",
    "musicbee": "MusicBee",
    "itunes": "iTunes",
}
TICKS = 10_000_000  # Windows counts time in 100 ns ticks


def available() -> str | None:
    try:
        from winrt.windows.media.control import (  # type: ignore  # noqa: F401  # Windows only
            GlobalSystemMediaTransportControlsSessionManager,
        )
    except Exception as e:
        return f"Windows' media controls could not be loaded ({type(e).__name__})"
    return None


def nice_name(aumid: str) -> str:
    """A player's name from its app id: Spotify.exe, SpotifyAB.Spotify..!Spotify,
    Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic, chrome."""
    raw = aumid.rsplit("!", 1)[-1] if "!" in aumid else aumid
    raw = raw.rsplit("\\", 1)[-1]
    if raw.lower().endswith(".exe"):
        raw = raw[:-4]
    stem = raw.rsplit(".", 1)[-1] if "." in raw else raw
    for key in (raw.lower(), stem.lower()):
        if key in NAMES:
            return NAMES[key]
    for key, name in NAMES.items():
        if key in aumid.lower():
            return name
    return stem[:1].upper() + stem[1:] if stem else "A player"


def seconds(value) -> float:
    """A WinRT TimeSpan as seconds (pywinrt gives a timedelta; older ticks)."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (int, float)):
        return float(value) / TICKS
    total = getattr(value, "total_seconds", None)
    result = total() if callable(total) else 0.0
    return float(result) if isinstance(result, (int, float)) else 0.0


def describe(
    aumid: str,
    status: int,
    title: str,
    artist: str,
    position: float,
    duration: float,
    can_next: bool,
    can_prev: bool,
    can_seek: bool,
) -> dict:
    """One player as forge_mpris.players() describes it."""
    state = (
        "playing" if status == PLAYING else "paused" if status == PAUSED else "stopped"
    )
    return {
        "bus": aumid,
        "name": nice_name(aumid),
        "desktop": "",
        "state": state,
        "position": max(0.0, round(position, 3)),
        "volume": 1.0,
        "canVolume": False,
        "canRaise": False,
        "canNext": bool(can_next),
        "canPrev": bool(can_prev),
        "canSeek": bool(can_seek),
        "title": title or "",
        "artist": artist or "",
        "artwork": "",
        "duration": round(duration, 3) if duration > 0 else 0,
    }


def position_now(
    position: float, updated, playing: bool, now: datetime | None = None
) -> float:
    """Windows reports where it was at last_updated_time; a playing track
    has moved on since."""
    if not playing or not isinstance(updated, datetime):
        return position
    now = now or datetime.now(timezone.utc)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return position + max(0.0, (now - updated).total_seconds())


async def _sessions():
    from winrt.windows.media.control import (  # type: ignore  # Windows only
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    manager = await Manager.request_async()
    return list(manager.get_sessions())


async def _players() -> list[dict]:
    found = []
    for s in await _sessions():
        try:
            props = await s.try_get_media_properties_async()
            pb = s.get_playback_info()
            tl = s.get_timeline_properties()
            status = int(pb.playback_status)
            controls = pb.controls
            start, end = seconds(tl.start_time), seconds(tl.end_time)
            pos = position_now(
                seconds(tl.position) - start, tl.last_updated_time, status == PLAYING
            )
            found.append(
                describe(
                    str(s.source_app_user_model_id),
                    status,
                    str(props.title or ""),
                    str(props.artist or props.album_artist or ""),
                    pos,
                    end - start,
                    controls.is_next_enabled,
                    controls.is_previous_enabled,
                    controls.is_playback_position_enabled,
                )
            )
        except Exception:  # one odd player never hides the rest
            continue
    return found


def players() -> list[dict]:
    try:
        return asyncio.run(_players())
    except Exception:
        return []


async def _control(bus: str, op: str, value: float | None) -> bool:
    for s in await _sessions():
        if str(s.source_app_user_model_id) != bus:
            continue
        if op == "toggle":
            return bool(await s.try_toggle_play_pause_async())
        if op == "play":
            return bool(await s.try_play_async())
        if op == "pause":
            return bool(await s.try_pause_async())
        if op == "next":
            return bool(await s.try_skip_next_async())
        if op == "prev":
            return bool(await s.try_skip_previous_async())
        if op == "seek" and value is not None:
            start = seconds(s.get_timeline_properties().start_time)
            return bool(
                await s.try_change_playback_position_async(
                    int((start + max(0.0, value)) * TICKS)
                )
            )
        return False  # volume and raise: not offered by Windows' media controls
    return False


def control(bus: str, op: str, value: float | None = None) -> bool:
    try:
        return asyncio.run(_control(bus, op, value))
    except Exception:
        return False
