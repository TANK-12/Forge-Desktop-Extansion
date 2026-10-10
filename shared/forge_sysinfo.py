"""What the widgets show about the laptop, read from /proc and /sys (S25).

Shared by forge.py (the hub's top bar and home widgets) and
desktop/particles.py (the desktop's widgets, which run without Forge), so
both read the numbers the same way. Standard library only.

    system_stats(previous)  CPU, memory, battery and the mains plug
    disks()                 the real drives that are mounted, fullest first
    NetMeter().read()       download and upload speed, and the Wi-Fi name
    jan_status()            whether Jan runs, and the models it has loaded
    temperatures()          CPU package, drive, battery and skin, in C; fans
    top_cpu(n)              the busiest processes over a second, busiest first
    snapshot(...)           all of them in one dict, as the widgets expect
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time

POWER_SUPPLY = "/sys/class/power_supply"

# Filesystems that hold files people keep; the rest of /proc/mounts is the
# kernel's own (proc, tmpfs, cgroup, overlay, squashfs and so on).
REAL_FS = {
    "ext4",
    "ext3",
    "btrfs",
    "xfs",
    "f2fs",
    "vfat",
    "exfat",
    "ntfs",
    "ntfs3",
    "fuseblk",
}


def system_stats(previous=None, power_supply: str = POWER_SUPPLY):
    """CPU, memory and battery for the top bar, read from /proc and /sys.

    CPU use is the change between two samples of /proc/stat, which is what a
    CPU percentage means, so the first call has no figure. Returns
    (stats, sample); pass the sample back in on the next call. On Windows
    (a Forge release) the same numbers come from Windows itself.
    """
    if sys.platform == "win32":
        return _windows_stats(previous)
    stats: dict[str, int | bool | None] = {
        "cpu": None,
        "mem": None,
        "battery": None,
        "charging": None,
        "plugged": None,
    }
    sample = previous
    try:
        with open("/proc/stat") as f:
            fields = [int(x) for x in f.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        if previous and total > previous[1]:
            busy = 1 - (idle - previous[0]) / (total - previous[1])
            stats["cpu"] = max(0, min(100, round(100 * busy)))
        sample = (idle, total)
    except (OSError, ValueError, IndexError):
        pass
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if rest.split():
                    info[key] = int(rest.split()[0])
        stats["mem"] = round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        pass
    for battery in sorted(glob.glob(os.path.join(power_supply, "BAT*"))):
        try:
            with open(os.path.join(battery, "capacity")) as f:
                stats["battery"] = int(f.read().strip())
            with open(os.path.join(battery, "status")) as f:
                stats["charging"] = f.read().strip() in ("Charging", "Full")
            break
        except (OSError, ValueError):
            continue
    # Plugged in, read from the mains adapter: a battery held at a charge
    # limit says "Not charging" while the laptop runs from the wall.
    for supply in sorted(glob.glob(os.path.join(power_supply, "*"))):
        try:
            with open(os.path.join(supply, "type")) as f:
                if f.read().strip() != "Mains":
                    continue
            with open(os.path.join(supply, "online")) as f:
                stats["plugged"] = f.read().strip() == "1"
            break
        except OSError:
            continue
    return stats, sample


def _unescape(path: str) -> str:
    """/proc/mounts writes a space as \\040 and a tab as \\011."""
    return path.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def mounts(text: str) -> list[tuple[str, str, str]]:
    """(device, mount point, filesystem) for each real drive in /proc/mounts
    text, one entry per device (btrfs mounts one device many times)."""
    seen: set[str] = set()
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in REAL_FS:
            continue
        device, where = parts[0], _unescape(parts[1])
        if device in seen or where.startswith(("/boot", "/efi", "/snap", "/var/lib")):
            continue
        seen.add(device)
        out.append((device, where, parts[2]))
    return out


def disks(mounts_file: str = "/proc/mounts", usage=shutil.disk_usage) -> list[dict]:
    """The mounted drives: where, a short name, how full, and space left.
    On Windows, every fixed or removable drive letter instead."""
    if sys.platform == "win32":
        return _windows_disks(usage)
    try:
        with open(mounts_file) as f:
            found = mounts(f.read())
    except OSError:
        found = []
    out = []
    for where in (entry[1] for entry in found):
        name = "System" if where == "/" else os.path.basename(where.rstrip("/"))
        row = _disk_row(where, name or where, usage)
        if row:
            out.append(row)
    return out


def _disk_row(where: str, name: str, usage) -> dict | None:
    try:
        u = usage(where)
    except OSError:
        return None
    if not u.total:
        return None
    return {
        "mount": where,
        "name": name,
        "used": round(100 * u.used / u.total),
        "free_gb": round(u.free / 1e9, 1),
        "total_gb": round(u.total / 1e9, 1),
    }


# --- Windows (a Forge release): the same numbers from Windows itself ---------
# Type checkers on Linux skip the bodies below the platform check; they are
# checked by running the Windows release (under Wine here).


def _windows_stats(previous=None):
    """system_stats on Windows: CPU from GetSystemTimes (whose kernel time
    includes the idle time), memory from GlobalMemoryStatusEx and the battery
    from GetSystemPowerStatus. The same keys and sample as on Linux."""
    stats: dict[str, int | bool | None] = {
        "cpu": None,
        "mem": None,
        "battery": None,
        "charging": None,
        "plugged": None,
    }
    if sys.platform != "win32":
        return stats, previous
    import ctypes
    from ctypes import wintypes

    def ticks(t) -> int:
        return (t.dwHighDateTime << 32) | t.dwLowDateTime

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "ullTotalPhys",
                "ullAvailPhys",
                "ullTotalPageFile",
                "ullAvailPageFile",
                "ullTotalVirtual",
                "ullAvailVirtual",
                "ullAvailExtendedVirtual",
            )
        ]

    class PowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    kernel32 = ctypes.windll.kernel32
    sample = previous
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        idle_now, total = ticks(idle), ticks(kernel) + ticks(user)
        if previous and total > previous[1]:
            busy = 1 - (idle_now - previous[0]) / (total - previous[1])
            stats["cpu"] = max(0, min(100, round(100 * busy)))
        sample = (idle_now, total)
    memory = MemoryStatus()
    memory.dwLength = ctypes.sizeof(MemoryStatus)
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)) and memory.ullTotalPhys:
        stats["mem"] = round(100 * (1 - memory.ullAvailPhys / memory.ullTotalPhys))
    power = PowerStatus()
    if kernel32.GetSystemPowerStatus(ctypes.byref(power)):
        if power.ACLineStatus in (0, 1):  # 255: unknown
            stats["plugged"] = power.ACLineStatus == 1
        # BatteryFlag 128: no battery, 255: unknown; 8: charging
        if power.BatteryFlag not in (128, 255) and power.BatteryLifePercent <= 100:
            stats["battery"] = power.BatteryLifePercent
            stats["charging"] = bool(power.BatteryFlag & 8) or (
                power.ACLineStatus == 1 and power.BatteryLifePercent == 100
            )
    return stats, sample


def _windows_disks(usage=shutil.disk_usage) -> list[dict]:
    """disks() on Windows: each fixed or removable drive letter, the one
    Windows runs from named System (and marked, for the widget's "Only the
    system drive")."""
    if sys.platform != "win32":
        return []
    import ctypes

    kernel32 = ctypes.windll.kernel32
    system = os.environ.get("SystemDrive", "C:").upper()
    mask = kernel32.GetLogicalDrives()
    out = []
    for n in range(26):
        root = f"{chr(65 + n)}:\\"
        # 2: removable, 3: fixed (not network, CD or RAM drives)
        if not mask & (1 << n) or kernel32.GetDriveTypeW(root) not in (2, 3):
            continue
        row = _disk_row(root, "System" if root[:2] == system else root[:2], usage)
        if row:
            row["system"] = root[:2] == system
            out.append(row)
    return out


def wifi_from_nmcli(out: str) -> str:
    """The Wi-Fi connection's name from `nmcli -t -f NAME,TYPE connection
    show --active` (NetworkManager names it after the network by default)."""
    for line in out.splitlines():
        name, sep, kind = line.rpartition(":")
        if sep and kind == "802-11-wireless":
            return name.replace("\\:", ":")
    return ""


class NetMeter:
    """Download and upload speed from two readings of /proc/net/dev, over
    every interface but loopback and virtual ones, plus the Wi-Fi name
    (asked of iwgetid or nmcli at most every 30 s; "" without either)."""

    SKIP = ("lo", "docker", "veth", "virbr", "br-", "tun", "tap")

    def __init__(self, dev_file: str = "/proc/net/dev", clock=time.monotonic):
        self.dev_file = dev_file
        self.clock = clock
        self.last: tuple[float, int, int] | None = None
        self.wifi = ""
        self.wifi_at = -1e9

    def totals(self) -> tuple[int, int, list[str]]:
        down = up = 0
        names = []
        with open(self.dev_file) as f:
            for line in f.readlines()[2:]:
                name, _, rest = line.partition(":")
                name = name.strip()
                if not rest or name.startswith(self.SKIP):
                    continue
                cols = rest.split()
                if len(cols) < 9:
                    continue
                down += int(cols[0])
                up += int(cols[8])
                names.append(name)
        return down, up, names

    def ssid(self) -> str:
        now = self.clock()
        if now - self.wifi_at < 30:
            return self.wifi
        self.wifi_at = now
        self.wifi = ""
        # iwgetid answers on both wpa_supplicant and iwd. nmcli's Wi-Fi list
        # is no use here: it says no for every network on iwd, and it starts
        # a scan that took 6 s (measured 2026-09-28), which froze Forge's
        # window each time; the active connection list answers in ~10 ms.
        for argv in (
            ["iwgetid", "-r"],
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
        ):
            if not shutil.which(argv[0]):
                continue
            try:
                out = subprocess.run(
                    argv, capture_output=True, text=True, timeout=3
                ).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            if argv[0] == "iwgetid":
                self.wifi = out.strip()
            else:
                self.wifi = wifi_from_nmcli(out)
            if self.wifi:
                break
        return self.wifi

    def read(self) -> dict:
        """{down, up} in bytes a second (None on the first reading), the
        interfaces counted, and the Wi-Fi name."""
        try:
            down, up, names = self.totals()
        except (OSError, ValueError):
            return {"down": None, "up": None, "ifaces": [], "wifi": ""}
        now = self.clock()
        rate: dict = {"down": None, "up": None}
        if self.last and now > self.last[0]:
            dt = now - self.last[0]
            rate = {
                "down": max(0, round((down - self.last[1]) / dt)),
                "up": max(0, round((up - self.last[2]) / dt)),
            }
        self.last = (now, down, up)
        return {**rate, "ifaces": names, "wifi": self.ssid()}


def jan_status(proc_dir: str = "/proc") -> dict:
    """Whether the Jan app runs, and the models with a process of their own
    (read from /proc; Jan itself is never asked, so this can't wake it)."""
    running = False
    for comm in glob.glob(os.path.join(proc_dir, "[0-9]*", "comm")):
        try:
            with open(comm) as f:
                name = f.read().strip().lower()
        except OSError:
            continue
        if name in ("jan", "jan-app") or name.startswith("jan "):
            running = True
            break
    try:
        import forge_jan

        loaded = forge_jan.loaded_models(proc_dir)
    except Exception:  # forge_jan missing or /proc unreadable: show nothing
        loaded = []
    return {"running": running or bool(loaded), "loaded": loaded}


HWMON = "/sys/class/hwmon"
THERMAL = "/sys/class/thermal"
# hwmon chip name -> what a person calls it (first sensor of the chip, or the
# labelled one). One entry per new sensor kind.
HWMON_NAMES = {"coretemp": "cpu", "k10temp": "cpu", "nvme": "drive", "BAT0": "battery"}
THERMAL_NAMES = {"TSKN": "skin", "TCPU": "cpu", "x86_pkg_temp": "cpu"}


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def temperatures(hwmon: str = HWMON, thermal: str = THERMAL) -> dict:
    """{"cpu": 61.0, "drive": 38.9, "battery": 27.0, "skin": 38.0,
    "fans": [2900]} in degrees C and rpm; missing sensors are left out. The
    CPU figure is the package sensor (coretemp "Package id 0")."""
    out: dict = {}
    fans: list[int] = []
    for chip in sorted(glob.glob(os.path.join(hwmon, "hwmon*"))):
        name = _read(os.path.join(chip, "name"))
        for fan in sorted(glob.glob(os.path.join(chip, "fan*_input"))):
            if _read(fan).isdigit():
                fans.append(int(_read(fan)))
        kind = HWMON_NAMES.get(name)
        if not kind or kind in out:
            continue
        inputs = sorted(glob.glob(os.path.join(chip, "temp*_input")))
        chosen = next(
            (
                i
                for i in inputs
                if _read(i.replace("_input", "_label")).startswith("Package")
            ),
            inputs[0] if inputs else "",
        )
        if chosen and _read(chosen).lstrip("-").isdigit():
            out[kind] = round(int(_read(chosen)) / 1000, 1)
    for zone in sorted(glob.glob(os.path.join(thermal, "thermal_zone*"))):
        kind = THERMAL_NAMES.get(_read(os.path.join(zone, "type")))
        value = _read(os.path.join(zone, "temp"))
        if kind and kind not in out and value.lstrip("-").isdigit():
            out[kind] = round(int(value) / 1000, 1)
    if fans:
        out["fans"] = fans
    return out


def _proc_times(proc_dir: str) -> dict[int, tuple[int, str, int]]:
    """pid -> (utime + stime ticks, comm, start time) for every process."""
    out = {}
    for entry in os.listdir(proc_dir):
        if not entry.isdigit():
            continue
        stat = _read(os.path.join(proc_dir, entry, "stat"))
        close = stat.rfind(")")
        if close < 0:
            continue
        comm = stat[stat.find("(") + 1 : close]
        rest = stat[close + 2 :].split()
        try:
            out[int(entry)] = (int(rest[11]) + int(rest[12]), comm, int(rest[19]))
        except (IndexError, ValueError):
            continue
    return out


def top_cpu(
    count: int = 10, seconds: float = 1.0, proc_dir: str = "/proc"
) -> list[dict]:
    """The busiest processes over `seconds`, busiest first: pid, comm, start
    (its start time, so a kill can check the pid was not reused), cpu (percent
    of one core, as top shows it), mem_mb and a short command line."""
    tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    before = _proc_times(proc_dir)
    time.sleep(seconds)
    after = _proc_times(proc_dir)
    rows = []
    for pid, (ticks, comm, start) in after.items():
        was = before.get(pid)
        if not was or was[2] != start:
            continue
        cpu = 100 * (ticks - was[0]) / tick / seconds
        rows.append((cpu, pid, comm, start))
    rows.sort(reverse=True)
    out = []
    for cpu, pid, comm, start in rows[:count]:
        rss = 0
        for line in _read(os.path.join(proc_dir, str(pid), "status")).splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) // 1024
        cmd = _read(os.path.join(proc_dir, str(pid), "cmdline")).replace("\0", " ")
        out.append(
            {
                "pid": pid,
                "comm": comm,
                "start": start,
                "cpu": round(cpu),
                "mem_mb": rss,
                "cmd": cmd.strip()[:160],
            }
        )
    return out


def start_time(pid: int, proc_dir: str = "/proc") -> int | None:
    """A process's start time (clock ticks since boot), None when it is gone."""
    stat = _read(os.path.join(proc_dir, str(pid), "stat"))
    rest = stat[stat.rfind(")") + 2 :].split()
    try:
        return int(rest[19])
    except (IndexError, ValueError):
        return None


def snapshot(previous=None, net: NetMeter | None = None, slow: dict | None = None):
    """The whole set the widgets read: system_stats plus network, and the
    slow parts (disks, Jan) from `slow` when given, which the caller
    refreshes less often. Returns (stats, sample) like system_stats."""
    stats, sample = system_stats(previous)
    out: dict = dict(stats)
    if net is not None:
        out["net"] = net.read()
    if slow:
        out.update(slow)
    return out, sample
