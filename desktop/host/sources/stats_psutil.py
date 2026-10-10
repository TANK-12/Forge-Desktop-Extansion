"""CPU, memory, battery, network, drives, temperatures and Jan through psutil,
for Windows and macOS (Relay task 130). The same dict as Linux's
shared/forge_sysinfo.snapshot(), so the widgets need no change. psutil runs
on Linux too, which is how the tests check it for real.

Not read here (the widgets already leave out what is missing): the Wi-Fi
name, and temperatures and fans on Windows and macOS (psutil has no sensors
there).
"""

from __future__ import annotations

import os
import sys
import time

import psutil

SLOW_EVERY = 30.0
# loopback and virtual interfaces are not the network (forge_sysinfo.NetMeter)
SKIP_NET = (
    "lo",
    "loopback",
    "docker",
    "veth",
    "virbr",
    "br-",
    "tun",
    "tap",
    "vethernet",
    "vmware",
    "virtualbox",
    "utun",
    "awdl",
    "llw",
    "bridge",
    "gif",
    "stf",
    "anpi",
    "ap1",
)
SKIP_MOUNTS = ("/System/Volumes/", "/private/var/vm", "/snap/", "/boot/efi")
TEMP_NAMES = {"coretemp": "cpu", "k10temp": "cpu", "nvme": "drive", "BAT0": "battery"}
JAN_NAMES = ("jan", "jan-app")
SERVERS = ("llama-server",)


def available() -> str | None:
    return None


def power() -> tuple[bool, int | None]:
    """(on battery, charge %); (False, None) on a machine without one."""
    try:
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return False, None
    plugged = b.power_plugged
    return (plugged is False), round(b.percent)


def battery() -> dict:
    try:
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return {"battery": None, "charging": None, "plugged": None}
    plugged = b.power_plugged
    return {
        "battery": round(b.percent),
        "charging": None if plugged is None else bool(plugged),
        "plugged": plugged,
    }


def counted(name: str) -> bool:
    return not name.lower().startswith(SKIP_NET)


class Net:
    """{down, up} bytes a second (None at first), the interfaces counted and
    the Wi-Fi name (not read here: "")."""

    def __init__(self, clock=time.monotonic, counters=None, stats=None):
        self.clock = clock
        self.counters = counters or (lambda: psutil.net_io_counters(pernic=True))
        self.stats = stats or psutil.net_if_stats
        self.last: tuple[float, int, int] | None = None

    def read(self) -> dict:
        try:
            per = self.counters()
            up_now = self.stats()
        except Exception:
            return {"down": None, "up": None, "ifaces": [], "wifi": ""}
        names = sorted(
            n for n in per if counted(n) and (n not in up_now or up_now[n].isup)
        )
        down = sum(per[n].bytes_recv for n in names)
        up = sum(per[n].bytes_sent for n in names)
        now = self.clock()
        rate: dict = {"down": None, "up": None}
        if self.last and now > self.last[0]:
            dt = now - self.last[0]
            rate = {
                "down": max(0, round((down - self.last[1]) / dt)),
                "up": max(0, round((up - self.last[2]) / dt)),
            }
        self.last = (now, down, up)
        return {**rate, "ifaces": names, "wifi": ""}


def drive_name(mount: str, system_drive: str = "") -> str:
    if mount == "/" or (
        system_drive and mount.upper().startswith(system_drive.upper())
    ):
        return "System"
    if len(mount) <= 3 and mount[1:2] == ":":  # D:\ on Windows
        return mount[:2]
    return os.path.basename(mount.rstrip("/\\")) or mount


def disks(parts=None, usage=None, system_drive: str | None = None) -> list[dict]:
    """The mounted drives in forge_sysinfo.disks()'s shape."""
    if system_drive is None:
        system_drive = (
            os.environ.get("SystemDrive", "") if sys.platform == "win32" else ""
        )
    usage = usage or psutil.disk_usage
    try:
        found = parts if parts is not None else psutil.disk_partitions(all=False)
    except Exception:
        found = []
    out, seen = [], set()
    for p in found:
        mount = p.mountpoint
        if mount in seen or "cdrom" in (p.opts or "") or mount.startswith(SKIP_MOUNTS):
            continue
        seen.add(mount)
        try:
            u = usage(mount)
        except Exception:  # an empty card reader, a drive that went away
            continue
        if not u.total:
            continue
        out.append(
            {
                "mount": mount,
                "name": drive_name(mount, system_drive),
                "used": round(100 * u.used / u.total),
                "free_gb": round(u.free / 1e9, 1),
                "total_gb": round(u.total / 1e9, 1),
            }
        )
    return out


def temperatures() -> dict:
    """forge_sysinfo.temperatures()'s shape where psutil has sensors (Linux,
    BSD); {} on Windows and macOS."""
    read = getattr(psutil, "sensors_temperatures", None)
    if read is None:
        return {}
    out: dict = {}
    try:
        chips = read()
    except Exception:
        return {}
    for chip, entries in chips.items():
        kind = TEMP_NAMES.get(chip)
        if not kind or kind in out or not entries:
            continue
        pick = next(
            (e for e in entries if (e.label or "").startswith("Package")), entries[0]
        )
        out[kind] = round(pick.current, 1)
    fans = getattr(psutil, "sensors_fans", None)
    try:
        rpm = [
            int(f.current) for chip in (fans() if fans else {}).values() for f in chip
        ]
    except Exception:
        rpm = []
    if rpm:
        out["fans"] = rpm
    return out


def _flag(argv: list[str], name: str) -> str:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return ""


def jan_status(procs=None) -> dict:
    """Whether Jan runs, and the models with a llama-server of their own
    (forge_sysinfo.jan_status()'s shape; Jan itself is never asked)."""
    running, loaded = False, []
    try:
        found = procs if procs is not None else psutil.process_iter(["name", "cmdline"])
        for proc in found:
            info = proc if isinstance(proc, dict) else getattr(proc, "info", None) or {}
            name = (info.get("name") or "").lower()
            if name.endswith(".exe"):
                name = name[:-4]
            if name in JAN_NAMES or name.startswith("jan "):
                running = True
            if name in SERVERS:
                argv = info.get("cmdline") or []
                alias = _flag(argv, "--alias")
                if alias and "--models-preset" not in argv:
                    loaded.append(alias)
    except Exception:
        pass
    return {"running": running or bool(loaded), "loaded": loaded}


class Stats:
    """The widgets' numbers every few seconds; the slow parts (drives, Jan,
    temperatures) at most every 30 s."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.net = Net(clock)
        self.slow: dict = {}
        self.slow_at: float | None = None
        psutil.cpu_percent(None)  # the first figure is meaningless; start the count

    def read(self) -> dict:
        out: dict = {"cpu": None, "mem": None}
        try:
            out["cpu"] = round(psutil.cpu_percent(None))
            out["mem"] = round(psutil.virtual_memory().percent)
        except Exception:
            pass
        out.update(battery())
        out["net"] = self.net.read()
        now = self.clock()
        if self.slow_at is None or now - self.slow_at > SLOW_EVERY:
            self.slow_at = now
            self.slow = {"disks": disks(), "jan": jan_status(), "temps": temperatures()}
        out.update(self.slow)
        return out
