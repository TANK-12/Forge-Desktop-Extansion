"""yt-dlp command lines and output, shared by everything in Forge that saves
YouTube audio or video: Media's Download tab and the hub's background music
("add from a YouTube link").

yt-dlp is the system one (/usr/bin/yt-dlp); ffmpeg turns audio into mp3.
Nothing here runs a process; callers do, so tests can hand in a fake.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

_PROGRESS_RE = re.compile(r"\[download\]\s+([0-9.]+)%")
# yt-dlp prints where the finished file went on one of these lines
_DEST_RE = re.compile(
    r'^\[(?:ExtractAudio|Merger|download)\] (?:Destination: |Merging formats into ")(.+?)"?$'
)
_ALREADY_RE = re.compile(r"^\[download\] (.+) has already been downloaded")


def ytdlp() -> Optional[str]:
    return shutil.which("yt-dlp")


def download_command(urls: list[str], folder: Path, kind: str = "audio") -> list[str]:
    """yt-dlp for a batch: mp3 with cover and tags for audio, an mp4 up to
    1080p for video. Names are the video titles; a file that already exists
    is skipped, so re-running a channel only fetches what is new."""
    exe = ytdlp() or "yt-dlp"
    out = str(folder / "%(title).150s [%(id)s].%(ext)s")
    cmd = [
        exe,
        "--no-warnings",
        "--newline",
        "--no-overwrites",
        "--no-playlist",
        "-o",
        out,
    ]
    if kind == "video":
        cmd += [
            "-f",
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
        ]
    else:
        cmd += [
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--embed-thumbnail",
            "--embed-metadata",
        ]
    return cmd + list(urls)


def progress_of(line: str) -> Optional[float]:
    m = _PROGRESS_RE.search(line)
    return float(m.group(1)) / 100.0 if m else None


def destination_of(line: str) -> Optional[str]:
    """The file a line says was written (the last one wins: the mp3 after
    the webm it came from), or None."""
    m = _DEST_RE.match(line.strip()) or _ALREADY_RE.match(line.strip())
    return m.group(1) if m else None
