"""Forge Desktop's per-OS registry (desktop/platforms.json) and the caps it
gives the page (Relay task 130, 2026-10-09).

caps is what the page (desktop/forge_desktop.js) and the settings window
(desktop/settings.html) read to hide what can't work here: every feature cut
on this OS or by this machine (no loopback sound device, no way behind the
desktop icons) with its reason, and the widget kinds and settings keys those
cuts take away. Nothing in the page names an OS; it only reads caps.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

PLATFORMS = Path(__file__).resolve().parents[1] / "platforms.json"


def load(path: Path = PLATFORMS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def this_os(platform: str = sys.platform) -> str:
    """The registry's name for the OS: linux, windows or macos."""
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    return "linux"


def caps(registry: dict, os_name: str, runtime_cut: dict | None = None) -> dict:
    """What the page and the settings window hide on `os_name`.

    runtime_cut: {feature: reason} found on this machine (a provider that
    does not work, the scenery that found no place behind the icons). A
    feature the OS entry cuts keeps the OS's reason."""
    entry = registry["platforms"][os_name]
    features = registry.get("features", {})
    cut = {**(runtime_cut or {}), **entry.get("cut", {})}
    cut = {k: str(v) for k, v in cut.items() if k in features}
    widgets: set[str] = set()
    settings = dict(entry.get("cut_settings", {}))
    for name, reason in cut.items():
        widgets.update(features[name].get("widgets", []))
        for key in features[name].get("settings", []):
            settings.setdefault(key, reason)
    return {
        "os": os_name,
        "label": entry.get("label", os_name),
        "cut": cut,
        "cut_labels": {name: features[name].get("label", name) for name in cut},
        "hide_widgets": sorted(widgets),
        "hide_settings": sorted(settings),
        "why": {**settings},
        "help": {
            key: texts.get(os_name, texts.get("*"))
            for key, texts in registry.get("help", {}).items()
            if texts.get(os_name, texts.get("*"))
        },
    }


def providers(registry: dict, os_name: str, kind: str) -> list[str]:
    return list(registry["platforms"][os_name].get("providers", {}).get(kind, []))


def pick(registry: dict, os_name: str, kind: str, package: str = __package__ or ""):
    """The first provider module of `kind` that works here, as (module, None),
    or (None, why) when none does. A provider is desktop/host/sources/
    <kind>_<name>.py with available() -> None when it works, else why not."""
    why = f"nothing reads {kind} on {os_name}"
    for name in providers(registry, os_name, kind):
        base = f"{package}.sources" if package else "sources"
        module = None
        try:
            module = importlib.import_module(f"{base}.{kind}_{name}")
            problem = module.available()
        except Exception as e:  # a missing library or OS part: try the next one
            problem = f"{name}: {type(e).__name__}: {e}"
        if problem is None and module is not None:
            return module, None
        why = str(problem)
    return None, why


def change_list(
    registry: dict, os_name: str, setting_labels: dict | None = None
) -> list[str]:
    """Plain lines for that OS's README: where each kind of data comes from
    and what is cut, with the reasons (the per-OS list the owner asked for).
    setting_labels: key -> the label Settings shows (forge_theme.SETTINGS)."""
    entry = registry["platforms"][os_name]
    labels = registry.get("provider_labels", {})
    features = registry.get("features", {})
    names = setting_labels or {}
    lines = []
    for kind, providers in entry.get("providers", {}).items():
        said = " or ".join(labels.get(n, n) for n in providers)
        lines.append(f"{features.get(kind, {}).get('label', kind)}: {said}")
    lines += [f"Partly: {line}" for line in entry.get("partial", [])]
    for feature, reason in entry.get("cut", {}).items():
        lines.append(
            f"Cut: {features.get(feature, {}).get('label', feature)} ({reason})"
        )
    for key, reason in entry.get("cut_settings", {}).items():
        lines.append(f"Hidden setting: {names.get(key, key)} ({reason})")
    return lines
