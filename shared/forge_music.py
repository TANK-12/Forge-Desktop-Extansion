"""The hub's background music and its visualiser feed, on the Python side.

forge.py routes page messages shaped {"music": {...}} to handle(), with a
push(event) that calls window.forgeMusic(event) in the hub. The requests:

  {"op": "add", "url": ...}     save a YouTube video's audio into the music
                                folder once (so no ads, and it plays offline)
                                with the same yt-dlp line Media uses, then
                                hand back the new track list
  {"op": "tracks"}              the music folder's tracks again, after files
                                were added some other way
  {"op": "levels", "on": bool}  what the laptop is playing, as bar levels
                                for the visualiser
  {"op": "serve"}               a localhost http address for the music
                                folder's files (below)
  {"op": "folder"}              the music folder's full path (made if
                                missing), so the hub can open it in Files

The levels come from cava reading the default output's monitor
(PipeWire/PulseAudio), so QBZ, a browser tab and a Forge app all count, not
only audio inside a Forge page. cava runs only while the hub says the
visualiser is drawing, and stops by itself when the hub goes quiet (the page
repeats "on" every few seconds), so a reloaded or closed page cannot leave it
running.

The music itself is played from a small http server on 127.0.0.1 rather than
from file:// links: WebKit treats every file:// link as another site, so the
visualiser's Web Audio reads silence from them. The server sends
Access-Control-Allow-Origin and answers byte ranges (seeking), and only hands
out playable files inside the music folder.
"""

from __future__ import annotations

import atexit
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlsplit

import forge_theme
import forge_ytdlp

Push = Callable[[dict], None]

# ----------------------------------------------------------------------------
# system audio levels


def cava_config(bars: int, fps: int, fast: bool = False) -> str:
    """cava printing one line per frame: `bars` numbers 0-1000 split by ;

    cava's default noise reduction of 77 held each level back by about 100 ms
    (tools/vis_latency.py, task 56), so the visualiser runs on a light 30 and
    the pages ease the drawn bars themselves (ForgeVisual.follow). `fast`
    (the Alt+V pulse) turns it down further still."""
    return (
        "[general]\n"
        f"bars = {bars}\n"
        f"framerate = {fps}\n"
        "autosens = 1\n"
        "[smoothing]\n"
        f"noise_reduction = {15 if fast else 30}\n"
        "[input]\n"
        "method = pulse\n"
        "source = auto\n"
        "[output]\n"
        "method = raw\n"
        "raw_target = /dev/stdout\n"
        "data_format = ascii\n"
        "ascii_max_range = 1000\n"
        "bar_delimiter = 59\n"
        "frame_delimiter = 10\n"
    )


def parse_frame(line: str, bars: int) -> Optional[list[float]]:
    """One cava line as levels 0..1, or None for a torn or partial line."""
    parts = [p for p in line.strip().split(";") if p != ""]
    if len(parts) != bars:
        return None
    try:
        return [min(1.0, max(0.0, int(p) / 1000)) for p in parts]
    except ValueError:
        return None


class SystemLevels:
    """One cava process at a time, its frames pushed as
    {"type": "levels", "v": [...]}. Silence is pushed once, not every frame."""

    KEEPALIVE_S = 15.0  # the hub repeats "on" every 5 s while drawing

    def __init__(self, exe: Optional[str] = None, popen=subprocess.Popen):
        self.exe = exe or shutil.which("cava")
        self.popen = popen
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.last_on = 0.0
        # the process stop() ended, so its exit is never reported as cava failing
        self.stopped = None
        self.conf: Optional[str] = None
        self.shape: tuple = ()

    def available(self) -> bool:
        return bool(self.exe)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(
        self, push: Push, bars: int = 32, fps: int = 30, fast: bool = False
    ) -> bool:
        self.last_on = time.monotonic()
        if self.running() and self.shape != (bars, fps, fast):
            self.stop()  # asked for another shape (the pulse wants it fast)
        with self.lock:
            if self.running():
                return True
            self.shape = (bars, fps, fast)
            if not self.exe:
                push({"type": "levels-off", "reason": "cava is not installed"})
                return False
            fd, self.conf = tempfile.mkstemp(prefix="forge-cava-", suffix=".conf")
            with os.fdopen(fd, "w") as f:
                f.write(cava_config(bars, fps, fast))
            cmd = [self.exe, "-p", self.conf]
            # cava's stdout is block-buffered into a pipe; line-buffer it so a
            # frame leaves the moment it is drawn
            if shutil.which("stdbuf"):
                cmd = ["stdbuf", "-oL"] + cmd
            self.proc = self.popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
            )
            proc = self.proc
        threading.Thread(
            target=self._pump, args=(proc, push, bars), daemon=True
        ).start()
        return True

    def _pump(self, proc, push: Push, bars: int) -> None:
        silent = False
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.monotonic() - self.last_on > self.KEEPALIVE_S:
                break
            v = parse_frame(line, bars)
            if v is None:
                continue
            quiet = not any(v)
            if quiet and silent:
                continue
            silent = quiet
            push({"type": "levels", "v": v})
        stopped_by_us = (
            proc is self.stopped
            or proc is not self.proc
            or time.monotonic() - self.last_on > self.KEEPALIVE_S
        )
        self._end(proc)
        if not stopped_by_us:
            push({"type": "levels-off", "reason": "cava stopped"})

    def _end(self, proc) -> None:
        with self.lock:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # a cava stuck writing to a pipe nobody reads ignores the
                    # TERM; reap it after the kill, or it stays a zombie
                    # child of forge.py (seen 2026-10-07, task 71)
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass  # stuck in the kernel: let cleanup go on
            if proc is self.proc:
                self.proc = None
                if self.conf:
                    Path(self.conf).unlink(missing_ok=True)
                    self.conf = None

    def stop(self) -> None:
        proc = self.proc
        if proc is not None:
            self.stopped = proc
            self._end(proc)


LEVELS = SystemLevels()
atexit.register(LEVELS.stop)

# ----------------------------------------------------------------------------
# the music folder


def music_folder() -> Path:
    return Path(os.path.expanduser(forge_theme.load()["music_folder"]))


def tracks_event(folder: Optional[Path] = None) -> dict:
    folder = folder or music_folder()
    return {
        "type": "tracks",
        "tracks": forge_theme.music_tracks(str(folder)),
        "note": forge_theme.music_note(str(folder)),
    }


def fetch_audio(url: str, folder: Path, push: Push, popen=subprocess.Popen) -> dict:
    """Save one video's audio as mp3 into `folder`. Pushes add-progress
    events on the way; returns {"ok", "file", "error"}."""
    folder.mkdir(parents=True, exist_ok=True)
    proc = popen(
        forge_ytdlp.download_command([url], folder, "audio"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdout is not None
    last, dest = "", None
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        frac = forge_ytdlp.progress_of(line)
        if frac is not None:
            push({"type": "add-progress", "frac": frac})
            continue
        dest = forge_ytdlp.destination_of(line) or dest
        last = line
    code = proc.wait()
    if code != 0:
        return {"ok": False, "file": None, "error": last or f"yt-dlp failed ({code})"}
    return {"ok": True, "file": dest, "error": None}


def add(
    url: str, push: Push, folder: Optional[Path] = None, popen=subprocess.Popen
) -> None:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        push({"type": "add-error", "error": "paste a YouTube link (https://...)"})
        return
    if not forge_ytdlp.ytdlp():
        push(
            {"type": "add-error", "error": "yt-dlp is not installed (pacman -S yt-dlp)"}
        )
        return
    folder = folder or music_folder()
    push({"type": "add-progress", "frac": 0.0})
    result = fetch_audio(url, folder, push, popen)
    if not result["ok"]:
        push({"type": "add-error", "error": result["error"]})
        return
    done = tracks_event(folder)
    done["type"] = "add-done"
    done["file"] = Path(result["file"]).as_uri() if result["file"] else ""
    push(done)


# ----------------------------------------------------------------------------
# the music folder over http


def track_path(uri: str, folder: Path) -> Optional[Path]:
    """The file a file:// link names, when it is a playable file inside
    `folder`; None otherwise."""
    parts = urlsplit(uri)
    if parts.scheme != "file":
        return None
    try:
        path = Path(unquote(parts.path)).resolve()
        root = folder.resolve()
    except (OSError, ValueError):
        return None
    if root not in path.parents or not path.is_file():
        return None
    if path.suffix.lower() not in forge_theme.MUSIC_TYPES:
        return None
    return path


def byte_range(header: str, size: int) -> Optional[tuple[int, int]]:
    """(first, last) for a single "bytes=a-b" Range header, or None."""
    if not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[6:].strip().partition("-")
    try:
        if first:
            a, b = int(first), int(last) if last else size - 1
        else:
            a, b = size - int(last), size - 1
    except ValueError:
        return None
    a, b = max(0, a), min(b, size - 1)
    return (a, b) if a <= b else None


class MusicServer:
    """GET /track?u=<file:// link> on 127.0.0.1, one per Forge window,
    started on the first "serve" request."""

    def __init__(self, folder: Callable[[], Path] = lambda: music_folder()):
        self.folder = folder
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.lock = threading.Lock()

    def start(self) -> str:
        with self.lock:
            if self.httpd is None:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
                self.httpd.daemon_threads = True
                threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
            port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/track?u="

    def stop(self) -> None:
        with self.lock:
            if self.httpd is not None:
                self.httpd.shutdown()
                self.httpd.server_close()
                self.httpd = None

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                pass

            def do_HEAD(self) -> None:
                self._send(body=False)

            def do_GET(self) -> None:
                self._send(body=True)

            def _send(self, body: bool) -> None:
                parts = urlsplit(self.path)
                uri = parse_qs(parts.query).get("u", [""])[0]
                path = (
                    track_path(uri, server.folder()) if parts.path == "/track" else None
                )
                if path is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    return
                size = path.stat().st_size
                span = byte_range(self.headers.get("Range", ""), size)
                first, last = span or (0, size - 1)
                self.send_response(206 if span else 200)
                kind = mimetypes.guess_type(path.name)[0]
                self.send_header("Content-Type", kind or "application/octet-stream")
                self.send_header("Content-Length", str(last - first + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                if span:
                    self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
                self.end_headers()
                if not body:
                    return
                left = last - first + 1
                try:
                    with open(path, "rb") as f:
                        f.seek(first)
                        while left > 0:
                            chunk = f.read(min(left, 1 << 16))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the player seeked or moved on

        return Handler


SERVER = MusicServer()
atexit.register(SERVER.stop)

# ----------------------------------------------------------------------------
# the page's requests


def _silent(stream: dict) -> bool:
    """Whether a `pactl -f json` sink input is muted or at 0% on every channel."""
    if stream.get("mute"):
        return True
    volumes = stream.get("volume") or {}
    values = [c.get("value") for c in volumes.values() if isinstance(c, dict)]
    return bool(values) and all(v == 0 for v in values)


def make_audible(app: str = "Forge", run=subprocess.run) -> list[int]:
    """Unmute this app's sound streams and lift any at 0% to 100% in the
    system mixer; returns the streams changed.

    #257 (the owner, 2026-09-27: the hub music "sometimes just not making
    noise"): WirePlumber keeps one saved volume per application name, and
    every page in Forge plays as "Forge". WebKit hands a media element's
    volume to the mixer, so a fade to 0 was saved as 0%, and each later
    Forge stream, the music's Web Audio one included, started silent while
    the visualiser still moved (measured 2026-09-28: the saved entry and the
    live stream both at 0%). Raising a live stream also rewrites the saved
    entry, so later streams start audible. A stream at any volume above 0 is
    left as the user set it."""
    try:
        out = run(
            ["pactl", "-f", "json", "list", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        streams = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    changed = []
    for stream in streams if isinstance(streams, list) else []:
        props = stream.get("properties") or {}
        if props.get("application.name") != app or not _silent(stream):
            continue
        index = str(stream.get("index"))
        try:
            if stream.get("mute"):
                run(["pactl", "set-sink-input-mute", index, "0"], timeout=5)
            values = [
                c.get("value")
                for c in (stream.get("volume") or {}).values()
                if isinstance(c, dict)
            ]
            if values and all(v == 0 for v in values):
                run(["pactl", "set-sink-input-volume", index, "100%"], timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        changed.append(int(stream.get("index")))
    return changed


def _audible_soon(push: Push) -> None:
    # the stream appears a moment after play(); look twice
    for wait in (1.0, 2.5):
        time.sleep(wait)
        changed = make_audible()
        if changed:
            push({"type": "audible", "streams": changed})
            return


def handle(req: dict, push: Push) -> None:
    op = req.get("op")
    if op == "levels":
        if req.get("on"):
            bars = int(req.get("bars") or 32)
            fps = int(req.get("fps") or 30)
            LEVELS.start(
                push,
                bars=max(8, min(128, bars)),
                fps=max(10, min(120, fps)),
                fast=bool(req.get("fast")),
            )
        else:
            LEVELS.stop()
    elif op == "add":
        threading.Thread(
            target=add, args=(str(req.get("url") or ""), push), daemon=True
        ).start()
    elif op == "tracks":
        push(tracks_event())
    elif op == "audible":
        threading.Thread(target=_audible_soon, args=(push,), daemon=True).start()
    elif op == "serve":
        push({"type": "serve", "base": SERVER.start()})
    elif op == "folder":
        folder = music_folder()
        folder.mkdir(parents=True, exist_ok=True)
        push({"type": "folder", "path": str(folder)})
