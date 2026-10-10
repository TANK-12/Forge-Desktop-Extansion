"""How busy other programs keep the GPU (the owner, 2026-09-29: Forge and the
desktop must ease off while a game, an LLM or a render uses the GPU).

Linux's DRM drivers (i915 on this laptop, amdgpu too) list every open GPU
client in /proc/<pid>/fdinfo/<fd> with its busy time per engine, for example
"drm-engine-render: 9602072480 ns". Two readings a few seconds apart give each
program's share of the GPU. Anyone can read their own processes, so this
needs no root and no intel_gpu_top.

Left out of the count:
    Forge's own drawing   the WebKit processes under forge.py and
                          desktop/particles.py, or Forge would ease off
                          because of itself
    gnome-shell           it composites every window, Forge's included
    the video engine      a YouTube tab decodes there without slowing
                          anything down

The busy share becomes a level (Easing), one rung per step the owner asked for:
    0  nothing changes
    1  the visualiser goes off
    2  the particles are halved
    3  frame rates come down to 25 (particles never under 20)
shared/forge_power.js applies the level; forge.py and desktop/particles.py
sample it here and pass it on. Programs that count as heavy whenever they
touch the GPU at all (games), and friendly names for the note the hub shows,
are in shared/gpu_heavy.json: a new one is one entry there.

    python3 shared/forge_gpuload.py      prints a reading every 2 s
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

PROC = Path("/proc")
REGISTRY = Path(__file__).with_name("gpu_heavy.json")
ENGINES = ("render", "compute", "gfx")  # gfx is amdgpu's name for render
OWN_MARKERS = ("Forge/forge.py", "Forge/desktop/particles.py")
RESCAN_SECONDS = 20.0  # every process's open files, looked at again
# busy share at which levels 1, 2 and 3 start
RUNGS = (0.25, 0.45, 0.65)
CALM_SECONDS = 10.0  # quiet this long before stepping back down one level
# The whole machine (the owner, 2026-10-07: "be smart with the fact that a lot of
# stuff is happening on GPU and CPU"): CPU use over every core, from one
# /proc/stat read per reading. Busy from CPU_ENTER % held CPU_ENTER_SECONDS,
# calm again under CPU_LEAVE % held CPU_LEAVE_SECONDS, so it never flickers.
CPU_ENTER, CPU_ENTER_SECONDS = 70.0, 10.0
CPU_LEAVE, CPU_LEAVE_SECONDS = 50.0, 20.0
CPU_LEVEL = 3  # a busy machine gets the reduced tier's last step


def load_registry(path: Path = REGISTRY) -> dict:
    """{"names": {match: friendly name}, "heavy": [match, ...]}; empty lists
    when the file is missing or broken."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    names, heavy = data.get("names"), data.get("heavy")
    return {
        "names": names if isinstance(names, dict) else {},
        "heavy": [str(h).lower() for h in heavy] if isinstance(heavy, list) else [],
    }


def parse_fdinfo(text: str) -> tuple[str, int] | None:
    """(client key, busy ns on the counted engines) from one fdinfo file, or
    None when that file is no GPU client. Two fds of one process can share a
    client; the key (device and client id) counts it once."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep:
            fields[name.strip()] = value.strip()
    if "drm-client-id" not in fields:
        return None
    busy = 0
    for engine in ENGINES:
        value = fields.get(f"drm-engine-{engine}", "")
        number = value.split()[0] if value else ""
        if number.isdigit():
            busy += int(number)
    return f"{fields.get('drm-pdev', '')}/{fields['drm-client-id']}", busy


def friendly(comm: str, registry: dict) -> str:
    low = comm.lower()
    for match, name in registry["names"].items():
        if str(match).lower() in low:
            return str(name)
    return comm


def is_heavy(comm: str, registry: dict) -> bool:
    low = comm.lower()
    return any(match in low for match in registry["heavy"])


class GpuMeter:
    """Samples the other programs' GPU share. Each sample reads only the GPU
    clients found before; every process's open files are listed again every
    RESCAN_SECONDS, and new processes on each sample, so a game that has just
    started is seen at once."""

    def __init__(
        self,
        proc: Path = PROC,
        clock: Callable[[], float] = time.monotonic,
        registry: dict | None = None,
        rescan: float = RESCAN_SECONDS,
    ):
        self.proc = Path(proc)
        self.clock = clock
        self.registry = registry if registry is not None else load_registry()
        self.rescan = rescan
        self.fds: dict[int, list[Path]] = {}
        self.looked: set[int] = set()
        self.scanned_at = float("-inf")
        self.last: dict[str, int] = {}
        self.last_at: float | None = None
        self.skip: dict[int, bool] = {}

    def _gpu_files(self, pid: int) -> list[Path]:
        fd_dir = self.proc / str(pid) / "fd"
        try:
            entries = os.listdir(fd_dir)
        except OSError:  # gone, or another user's
            return []
        out = []
        for fd in entries:
            try:
                target = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if target.startswith("/dev/dri/"):
                out.append(self.proc / str(pid) / "fdinfo" / fd)
        return out

    def _scan(self, now: float) -> None:
        try:
            alive = {int(p) for p in os.listdir(self.proc) if p.isdigit()}
        except OSError:
            alive = set()
        if now - self.scanned_at >= self.rescan:
            self.scanned_at = now
            self.looked.clear()
            self.fds.clear()
        for pid in alive - self.looked:
            self.looked.add(pid)
            files = self._gpu_files(pid)
            if files:
                self.fds[pid] = files
        self.looked &= alive
        for pid in [p for p in self.fds if p not in alive]:
            del self.fds[pid]
            self.skip.pop(pid, None)

    def _read(self, pid: int, name: str) -> str:
        try:
            return (self.proc / str(pid) / name).read_text(errors="replace")
        except OSError:
            return ""

    def comm(self, pid: int) -> str:
        return self._read(pid, "comm").strip()

    def _parent(self, pid: int) -> int:
        stat = self._read(pid, "stat")
        # the name in brackets can hold spaces; the fields after it cannot
        rest = stat[stat.rfind(")") + 2 :].split()
        return int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0

    def ours(self, pid: int) -> bool:
        """gnome-shell, or one of Forge's own WebKit processes."""
        if pid in self.skip:
            return self.skip[pid]
        comm = self.comm(pid)
        mine = comm == "gnome-shell"
        if not mine and comm.startswith("WebKit"):
            at = pid
            for _ in range(8):  # up through bwrap to forge.py or particles.py
                at = self._parent(at)
                if at <= 1:
                    break
                line = self._read(at, "cmdline").replace("\0", " ")
                if any(marker in line for marker in OWN_MARKERS):
                    mine = True
                    break
        self.skip[pid] = mine
        return mine

    def shares(self) -> dict[int, float]:
        """Every GPU client's busy share (0..1) since the last call, by pid,
        Forge's own and gnome-shell included. Empty on the first call."""
        now = self.clock()
        self._scan(now)
        totals: dict[str, int] = {}
        owner: dict[str, int] = {}
        for pid, files in self.fds.items():
            for path in files:
                try:
                    got = parse_fdinfo(path.read_text(errors="replace"))
                except OSError:
                    continue
                if got:
                    totals[got[0]] = got[1]
                    owner[got[0]] = pid
        shares: dict[int, float] = {}
        if self.last_at is not None and now > self.last_at:
            span = (now - self.last_at) * 1e9
            for key, busy in totals.items():
                grew = busy - self.last.get(key, busy)
                if grew > 0:
                    pid = owner[key]
                    shares[pid] = shares.get(pid, 0.0) + grew / span
        self.last, self.last_at = totals, now
        return shares

    def sample(self) -> dict:
        """{"busy": share 0..1 of the other programs, "by": the busiest one's
        name, "heavy": a program from the heavy list is using the GPU}."""
        shares = self.shares()
        others = {pid: s for pid, s in shares.items() if not self.ours(pid)}
        if not others:
            return {"busy": 0.0, "by": "", "heavy": False}
        top = max(others, key=lambda p: others[p])
        heavy = any(
            s > 0.03 and is_heavy(self.comm(pid), self.registry)
            for pid, s in others.items()
        )
        return {
            "busy": round(min(1.0, sum(others.values())), 3),
            "by": friendly(self.comm(top), self.registry),
            "heavy": heavy,
        }


class Easing:
    """The level to run at: straight up to what the load asks for, then back
    down one level at a time, each after CALM_SECONDS of less load, so the
    effects do not flicker on and off with a busy moment."""

    def __init__(self, calm: float = CALM_SECONDS, rungs=RUNGS):
        self.calm = calm
        self.rungs = rungs
        self.level = 0
        self.by = ""
        self.calm_since: float | None = None

    def target(self, busy: float, heavy: bool) -> int:
        return 3 if heavy else sum(busy >= r for r in self.rungs)

    def feed(self, reading: dict, now: float) -> dict:
        want = self.target(float(reading.get("busy", 0)), bool(reading.get("heavy")))
        if want >= self.level:
            if want > self.level or reading.get("by"):
                self.by = reading.get("by", "") or self.by
            self.level = want
            self.calm_since = None
        elif self.calm_since is None:
            self.calm_since = now
        elif now - self.calm_since >= self.calm:
            self.level -= 1
            self.calm_since = now
        if self.level == 0:
            self.by = ""
        return {"level": self.level, "by": self.by, "busy": reading.get("busy", 0)}


def cpu_times(proc: Path = PROC) -> tuple[int, int] | None:
    """(busy, total) jiffies over every core from /proc/stat, or None."""
    try:
        with open(Path(proc) / "stat", encoding="ascii") as f:
            fields = f.readline().split()
    except (OSError, ValueError):
        return None
    if not fields or fields[0] != "cpu":
        return None
    nums = [int(x) for x in fields[1:9] if x.isdigit()]
    if len(nums) < 5:
        return None
    idle = nums[3] + nums[4]  # idle and iowait
    return sum(nums) - idle, sum(nums)


class CpuBusy:
    """Whether the whole machine is busy, with hysteresis: busy once the CPU
    has stayed at or over `enter` % for `enter_for` seconds, calm again once
    it has stayed under `leave` % for `leave_for` seconds. In between, or
    after a short spike or dip, it keeps what it was."""

    def __init__(
        self,
        enter: float = CPU_ENTER,
        enter_for: float = CPU_ENTER_SECONDS,
        leave: float = CPU_LEAVE,
        leave_for: float = CPU_LEAVE_SECONDS,
    ):
        self.enter, self.enter_for = enter, enter_for
        self.leave, self.leave_for = leave, leave_for
        self.busy = False
        self.since: float | None = None  # when the current run over/under began

    def feed(self, pct: float, now: float) -> bool:
        heading = pct >= self.enter if not self.busy else pct < self.leave
        if not heading:
            self.since = None
            return self.busy
        if self.since is None:
            self.since = now
        if now - self.since >= (self.leave_for if self.busy else self.enter_for):
            self.busy = not self.busy
            self.since = None
        return self.busy


class GpuLoad:
    """A meter and its easing together: what forge.py and particles.py hold.
    Its reading also carries the whole machine's CPU (CpuBusy): a busy
    machine raises the level to CPU_LEVEL with "reason": "cpu"; otherwise
    the reason is "gpu" while the other programs' GPU load sets a level."""

    def __init__(
        self,
        meter: GpuMeter | None = None,
        easing: Easing | None = None,
        cpu: CpuBusy | None = None,
    ):
        self.meter = meter or GpuMeter()
        self.easing = easing or Easing()
        self.cpu = cpu or CpuBusy()
        self.cpu_last = cpu_times(self.meter.proc)

    def cpu_percent(self) -> float | None:
        now = cpu_times(self.meter.proc)
        last, self.cpu_last = self.cpu_last, now
        if not now or not last or now[1] <= last[1]:
            return None
        return 100.0 * (now[0] - last[0]) / (now[1] - last[1])

    def read(self) -> dict:
        try:
            reading = self.meter.sample()
        except Exception as e:  # a strange /proc never stops the caller
            print(f"forge_gpuload: {e}", file=sys.stderr)
            reading = {"busy": 0.0, "by": "", "heavy": False}
        now = self.meter.clock()
        out = self.easing.feed(reading, now)
        out["reason"] = "gpu" if out["level"] else ""
        pct = self.cpu_percent()
        if pct is not None:
            out["cpu"] = round(pct, 1)
            if self.cpu.feed(pct, now) and out["level"] <= CPU_LEVEL:
                out.update(level=CPU_LEVEL, reason="cpu")
        elif self.cpu.busy and out["level"] <= CPU_LEVEL:
            out.update(level=CPU_LEVEL, reason="cpu")
        return out


def top_gpu(
    count: int = 10, seconds: float = 1.0, meter: GpuMeter | None = None
) -> list[dict]:
    """The busiest GPU clients over `seconds`, busiest first: pid, comm,
    friendly name, share (0..1) and whether it is Forge's own or the shell.
    For Relay's "top gpu" list (2026-10-07)."""
    meter = meter or GpuMeter()
    meter.shares()
    time.sleep(seconds)
    shares = meter.shares()
    out = []
    for pid in sorted(shares, key=lambda p: -shares[p])[:count]:
        comm = meter.comm(pid)
        out.append(
            {
                "pid": pid,
                "comm": comm,
                "name": friendly(comm, meter.registry),
                "share": round(shares[pid], 3),
                "ours": meter.ours(pid),
            }
        )
    return out


def battery_percent(root: Path = Path("/sys/class/power_supply")) -> int | None:
    """The first battery's charge, or None without one."""
    for supply in sorted(Path(root).glob("*")):
        try:
            if (supply / "type").read_text().strip() == "Battery":
                return int((supply / "capacity").read_text().strip())
        except (OSError, ValueError):
            continue
    return None


if __name__ == "__main__":
    load = GpuLoad()
    while True:
        print(json.dumps(load.read()), flush=True)
        time.sleep(2)
