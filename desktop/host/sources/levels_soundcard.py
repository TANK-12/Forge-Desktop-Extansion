"""The sound playing now as the visualiser's 32 bars (Relay task 130).

Linux reads cava (shared/forge_music.SystemLevels); here the sound card's
own loopback is read through the soundcard library and turned into bars
with a numpy FFT: WASAPI loopback of the default speaker on Windows, a
loopback input device (BlackHole, Loopback, Soundflower) on a Mac, the
default output's monitor on Linux (how the tests hear it). Same events as
SystemLevels: {"type": "levels", "v": [0..1] * bars}, silence once.

A Mac without a loopback device has nothing to hear: available() says so
and the visualiser is cut there (desktop/platforms.json "levels").
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

import numpy as np

RATE = 48000
LOW, HIGH = 40.0, 16000.0
MAC_LOOPBACKS = ("blackhole", "loopback", "soundflower", "background music")


def _soundcard():
    # soundcard names its PulseAudio client from sys.argv, which is empty
    # in an embedded or frozen Python
    if not sys.argv or not sys.argv[0]:
        sys.argv = ["forge-desktop"]
    import soundcard

    return soundcard


def find_loopback(sc=None):
    """The device that hears what the speakers play, or None."""
    sc = sc or _soundcard()
    if sys.platform == "darwin":
        for mic in sc.all_microphones():
            if any(n in mic.name.lower() for n in MAC_LOOPBACKS):
                return mic
        return None
    try:
        speaker = sc.default_speaker()
        return sc.get_microphone(id=str(speaker.name), include_loopback=True)
    except Exception:
        loops = [
            m
            for m in sc.all_microphones(include_loopback=True)
            if getattr(m, "isloopback", False)
        ]
        return loops[0] if loops else None


def available() -> str | None:
    try:
        mic = find_loopback()
    except Exception as e:
        return f"the sound card could not be read ({type(e).__name__})"
    if mic is None:
        if sys.platform == "darwin":
            return (
                "macOS lets no app hear the sound itself: install a free "
                "loopback device such as BlackHole and play through it"
            )
        return "no loopback sound device"
    return None


def edges(n: int, rate: int = RATE, size: int = 2048) -> list[int]:
    """FFT bin edges for n bands spaced evenly in pitch from LOW to HIGH."""
    hz = np.geomspace(LOW, min(HIGH, rate / 2 - 1), n + 1)
    bins = np.clip(np.round(hz * size / rate).astype(int), 1, size // 2)
    out = [int(bins[0])]
    for b in bins[1:]:
        out.append(max(int(b), out[-1] + 1))
    return out


def bars(samples: np.ndarray, n: int = 32, rate: int = RATE) -> np.ndarray:
    """Loudness per band (log scale, not yet 0..1) of a mono block."""
    size = 2048
    block = np.zeros(size)
    take = samples[-size:]
    block[: len(take)] = take
    spectrum = np.abs(np.fft.rfft(block * np.hanning(size)))
    cuts = edges(n, rate, size)
    out = np.empty(n)
    for i in range(n):
        lo, hi = cuts[i], max(cuts[i + 1], cuts[i] + 1)
        out[i] = spectrum[lo:hi].mean() if hi <= len(spectrum) else 0.0
    # higher bands carry less energy in music; tilt them up a little
    out *= np.linspace(1.0, 3.0, n)
    return np.log1p(out)


class Gain:
    """Auto sensitivity like cava's: the loudest recent frame reaches the
    top, and the scale relaxes slowly when the music gets quieter."""

    def __init__(self, floor: float = 0.6, fall: float = 0.995):
        self.peak = floor
        self.floor = floor
        self.fall = fall

    def __call__(self, levels: np.ndarray) -> list[float]:
        top = float(levels.max()) if levels.size else 0.0
        self.peak = max(self.floor, top, self.peak * self.fall)
        scaled = np.clip(levels / self.peak, 0.0, 1.0)
        return [round(float(v), 3) for v in scaled]


class Levels:
    """One reader thread at a time; start() again with a new shape restarts."""

    def __init__(self, open_mic: Callable | None = None):
        self.open_mic = open_mic or find_loopback
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.shape = (32, 30)
        self.push: Callable[[dict], None] = lambda _e: None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(
        self, push: Callable[[dict], None], bars_n: int = 32, fps: int = 30
    ) -> None:
        self.push = push
        if self.running() and self.shape == (bars_n, fps):
            return
        self.stop()
        self.shape = (bars_n, fps)
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(
            target=self.loop, args=(self.stop_flag,), daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_flag.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(1.5)
        self.thread = None

    def loop(self, stop: threading.Event) -> None:
        n, fps = self.shape
        gain = Gain()
        silent = False
        try:
            mic = self.open_mic()
            if mic is None:
                self.push({"type": "levels-off", "reason": "no loopback sound device"})
                return
            with mic.recorder(
                samplerate=RATE, channels=1, blocksize=RATE // fps
            ) as rec:
                while not stop.is_set():
                    data = rec.record(numframes=RATE // fps)
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    if not np.any(np.abs(mono) > 1e-4):
                        if not silent:
                            self.push({"type": "levels", "v": [0.0] * n})
                        silent = True
                        continue
                    silent = False
                    self.push({"type": "levels", "v": gain(bars(mono, n))})
        except Exception as e:
            self.push(
                {
                    "type": "levels-off",
                    "reason": f"the sound stopped ({type(e).__name__})",
                }
            )
