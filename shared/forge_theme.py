"""How Forge looks: the wallpaper, the accent colour and the glass.

One module, shared by `forge.py` (which applies the theme at launch) and the
Settings app (which changes it). Standard library only; images are handled by
ImageMagick's `magick`, which is already on this machine for the desktop.

WHERE THINGS LIVE
    settings   ~/.config/forge/settings.json - the user's choices. Outside
               ~/Forge on purpose: user data never lives in the code folder.
    cache      ~/.cache/forge/ - wallpaper copies, blurred copies, thumbnails
               and palettes. Safe to delete; everything is regenerated.
    hub        hub/theme.js - the theme as a script global. The hub is loaded
               over file://, where fetch() of a sibling file is blocked, so the
               theme arrives exactly the way status.js does.

WHY THE WALLPAPER IS COPIED
    The wallpapers in ~/Pictures/Wallpapers are 4K-8K images of 5-28 MB.
    Painting one of those as a page background is a slow start on every
    launch. A 2560 px JPEG copy is about 200 KB and decodes instantly.

WHY THERE IS A BLURRED COPY
    Apps sit on frosted glass. Blurring live with `backdrop-filter` redraws on
    every frame and is heavy on this laptop's Iris Xe; blurring once with
    ImageMagick costs about 0.1 s and then nothing ever again.

WHERE THE COLOUR COMES FROM
    The wallpaper's own palette, the same idea as matugen on the desktop. It is
    computed here rather than by running matugen, because matugen applies its
    templates - running it would rewrite the desktop's theme as a side effect.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import os
from urllib.parse import quote, unquote, urlsplit
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HOME = Path.home()
CONFIG = Path(os.environ.get("FORGE_CONFIG", HOME / ".config/forge/settings.json"))
CACHE = Path(os.environ.get("FORGE_CACHE", HOME / ".cache/forge"))
AUTOSTART = Path(
    os.environ.get("FORGE_AUTOSTART", HOME / ".config/autostart/forge.desktop")
)
FORGE_PY = Path(__file__).resolve().parents[1] / "forge.py"
WALLPAPER_DIR = Path(
    os.environ.get("FORGE_WALLPAPERS", HOME / "Pictures/Wallpapers")
).expanduser()
# The desktop shell records its current wallpaper here. Following it is the
# default, so Forge starts on the same picture as the desktop.
DESKTOP_WALLPAPER = Path(
    os.environ.get(
        "FORGE_DESKTOP_WALLPAPER", HOME / ".local/state/caelestia/wallpaper/path.txt"
    )
)
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp"}
FALLBACK_ACCENT = "#6ea8fe"
# How the hub can look: one stylesheet per look in hub/looks/, found by
# scanning, so a new look is one new file. Its first line names it:
#     /* look: Material 3 | Tonal colours from the wallpaper, big rounded shapes */
LOOKS_DIR = Path(
    os.environ.get("FORGE_LOOKS", Path(__file__).resolve().parent.parent / "hub/looks")
)
LOOK_ORDER = ("classic",)  # listed first; the rest follow by name


# The glass kinds (S26): one stylesheet per kind in shared/glass/, found the
# same way, first line "/* glass: Liquid | about */". "flat" has no file.
GLASS_DIR = Path(
    os.environ.get("FORGE_GLASS", Path(__file__).resolve().parent / "glass")
)


def file_link(path: Path) -> str:
    """A link the hub and the apps can load a local file from. forge.py loads
    the hub over file://, so a file:// link; the portable launcher serves the
    hub over http and sets FORGE_FILE_BASE to its own file address instead
    (an http page may not load file:// links)."""
    base = os.environ.get("FORGE_FILE_BASE")
    if base:
        return base + quote(str(path), safe="")
    return path.as_uri()


def _styles(folder: Path, tag: str) -> dict[str, dict[str, str]]:
    """Each stylesheet in `folder` whose first line is `/* <tag>: Label | about */`."""
    found: dict[str, dict[str, str]] = {}
    files = sorted(folder.glob("*.css")) if folder.is_dir() else []
    for path in files:
        try:
            first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        except OSError:
            continue
        head = first.strip().removeprefix("/*").removesuffix("*/").strip()
        if not head.lower().startswith(tag + ":"):
            continue
        label, _, about = head[len(tag) + 1 :].partition("|")
        found[path.stem] = {"label": label.strip() or path.stem, "about": about.strip()}
    return found


def list_glass(directory: Path | None = None) -> dict[str, dict[str, str]]:
    """Every glass kind's label and description, Flat (the look's own) first."""
    kinds = {"flat": {"label": "Flat", "about": "The look's own panes, no blur."}}
    kinds.update(
        (k, v)
        for k, v in _styles(directory or GLASS_DIR, "glass").items()
        if k != "flat"
    )
    return kinds


def list_looks(directory: Path | None = None) -> dict[str, dict[str, str]]:
    """Every look's label and one-line description, Classic always present."""
    looks = _styles(directory or LOOKS_DIR, "look")
    looks.setdefault(
        "classic", {"label": "Classic", "about": "How Forge looked first."}
    )
    order = [k for k in LOOK_ORDER if k in looks] + sorted(
        k for k in looks if k not in LOOK_ORDER
    )
    return {k: looks[k] for k in order}


def _choice(default: str, labels: dict[str, str], **extra) -> dict:
    return {
        "default": default,
        "kind": "choice",
        "choices": list(labels),
        "labels": labels,
        **extra,
    }


COLOURS, LAYOUT, BAR, HOME_SCREEN, FULL = (
    "Colours inside apps",
    "Glass and layout",
    "Top bar and dock",
    "Home screen",
    "Full screen",
)
SIZE = "Size"
KEYS = "Forge keys"
STARTUP = "Starting and closing"
ZEN = "Zen mode"
AMBIENT = "Background mode"
MUSIC = "Music and visualiser"
APPS = "Apps"
SPEED = "Speed and detail"
DESKTOP_PARTICLES = "On the desktop"
WIDGETS = "Widgets"

# The particle kinds, shared by the home screen and the desktop's own pick.
PARTICLE_LABELS = {
    "off": "Off",
    "auto": "Match the wallpaper",
    "soft": "Soft drift",
    "lively": "Lively",
    "embers": "Forge embers",
    "constellation": "Constellation",
    "petals": "Falling petals",
    "rain": "Rain",
    "snow": "Snow",
    "rays": "Light rays",
    "fireflies": "Fireflies",
    "dust": "Floating dust",
    "leaves": "Autumn leaves",
    "petalstorm": "Petal storm",
    "bubbles": "Bubbles",
    "stars": "Twinkling stars",
    "emberstorm": "Ember storm",
    "glitter": "Glitter",
    "lanterns": "Paper lanterns",
    "wisps": "Spirit wisps",
    "galaxy": "Galaxy",
}

# The visualiser styles (shared/forge_visual.js draws them), shared by the
# home screen, background mode, the desktop and the visualiser widget.
VISUALISER_LABELS = {
    "bars": "Bars",
    "ring": "Ring",
    "wave": "Wave",
    "mirror": "Mirror",
    "aurora": "Aurora",
    "orbit": "Orbit",
    "terrain": "Terrain",
    "spectrum": "Spectrum",
    "nebula": "Nebula",
}

# Forge's own keys: action -> keys, written the way people say them. forge.py
# catches these on the window, before the page, so they work inside every app;
# the hub_keys setting overrides any of them. Plain "f" is gone on purpose: it
# went full screen whenever it was pressed outside a text box.
HUB_KEYS = {
    "fullscreen": "Alt+F, F11",
    "prev-app": "Alt+Left",
    "next-app": "Alt+Right",
    **{f"app-{n}": f"Alt+{n}" for n in range(1, 10)},
    "overview": "Alt+Space",
    # the owner (2026-09-29): "alt h goes to desktop and alt s goes to settings"
    "home": "Alt+H",
    "settings": "Alt+S",
    "zen": "Alt+Z",
    "ambient": "Alt+B",
    "pulse": "Alt+V",
    # S25 (the owner, 2026-09-28): "a keybind to turn off animations and
    # particles in that order and then back, Alt+P to turn off particles and
    # Alt+M to turn off the visualiser"; in Alt+V, Alt+M opens the visuals popup
    "effects": "Alt+E",
    "particles-toggle": "Alt+P",
    "visualiser-toggle": "Alt+M",
    "visuals": "Alt+Shift+M",
    # the owner (2026-09-28): "what keybind is there to stop all animations,
    # make one": the Animations switch (Settings > Glass and layout)
    "animations": "Alt+A",
    # the owner (2026-10-07): "an Alt+W widget-only page and Alt+Q close apps"
    "widgets": "Alt+W",
    "close-app": "Alt+Q",
    "quit": "Ctrl+Shift+Q",
    "restart": "Ctrl+Shift+R",
}
HUB_KEY_LABELS = {
    "fullscreen": "Full screen",
    "prev-app": "Previous open app",
    "next-app": "Next open app",
    **{f"app-{n}": f"Open app {n} on the bar" for n in range(1, 10)},
    "overview": "Show open apps",
    "home": "Home screen",
    "settings": "Settings",
    "zen": "Zen mode (leaving goes back to the app you were in)",
    "ambient": "Background mode (leaving goes back to the app you were in)",
    "pulse": "Background pulse (lights to the music)",
    "effects": "Visualiser off, then particles off, then both back",
    "particles-toggle": "Particles on or off",
    "visualiser-toggle": "Visualiser on or off (in Alt+V: visuals settings)",
    "visuals": "Visuals settings popup",
    "animations": "All animations off or on",
    "widgets": "Widgets page (press again to go back)",
    "close-app": "Close the open app",
    "quit": "Quit Forge",
    "restart": "Restart Forge",
}

# The size presets, smallest first. Text leads and layout follows slowly:
# the owner wants easier reading without everything growing huge. Saved
# customs (the size_presets setting) come after these.
SIZE_KEYS = ("ui_scale", "text_scale", "icon_scale")
SIZE_PRESETS = [
    {"name": "Small", "ui_scale": 0.9, "text_scale": 0.9, "icon_scale": 0.9},
    {"name": "Default", "ui_scale": 1.0, "text_scale": 1.0, "icon_scale": 1.0},
    {"name": "Comfortable", "ui_scale": 1.0, "text_scale": 1.15, "icon_scale": 1.1},
    {"name": "Large", "ui_scale": 1.1, "text_scale": 1.25, "icon_scale": 1.25},
    {"name": "Extra large", "ui_scale": 1.25, "text_scale": 1.4, "icon_scale": 1.4},
]

# The sharpest video the Browser's pages may pick, by its shorter side.
VIDEO_HEIGHTS = {
    "2160": "Any, up to 4K",
    "1440": "1440p",
    "1080": "1080p",
    "720": "720p",
    "480": "480p",
    "360": "360p",
}

# Settings with a battery twin (the owner, 2026-09-27: "more lod and frame rate
# options on battery and off battery"): plugged-in key -> (battery key, rule).
# "min": unplugged, the lower of the two applies, so the battery one can only
# lighten; "scale": unplugged, the plugged-in value is multiplied by the
# battery share. The theme carries this table as power_pairs, so the hub and
# the desktop particles (shared/forge_power.js) and forge.py (power_values
# below) apply the same rules without their own copy.
POWER_PAIRS: dict[str, tuple[str, str]] = {
    "max_fps": ("battery_fps", "min"),
    "particle_fps": ("battery_particle_fps", "min"),
    "desktop_particle_fps": ("battery_particle_fps", "min"),
    "particle_amount": ("battery_particles", "scale"),
    "desktop_particle_amount": ("battery_particles", "scale"),
    "video_fps": ("battery_video_fps", "min"),
    "video_height": ("battery_video_height", "min"),
    "smooth_scroll": ("battery_smooth_scroll", "min"),
}

# Every setting, its default, and how it is kept sane. Adding a setting is one
# entry here: the Settings app builds its form from what `describe()` returns
# (grouped by `group`, labelled by `label`), and `theme()` hands every value to
# the hub and the apps. `custom` marks the few with a hand-made control;
# `also` lists more groups whose card shows the same control (Speed and detail
# collects every frame rate and detail slider that way).
# `unit` tells the form how to print a number: percent (of 1), whole_percent
# (of 100), solid, px, ms, times.
SETTINGS: dict[str, dict] = {
    "wallpaper": {"default": "", "kind": "text", "custom": True},  # "" = desktop's
    "accent": {"default": "", "kind": "colour", "custom": True},  # "" = wallpaper's
    "unify_accent": {"default": True, "kind": "bool", "custom": True},
    # --- colours inside apps
    "theme_source": _choice(
        "wallpaper",
        {"wallpaper": "Follow the wallpaper", "neutral": "Plain dark grey"},
        group=COLOURS,
        label="Panel colours",
        help="Tints every app's panels, borders and text with the wallpaper's colour.",
    ),
    "tint": {
        "default": 0.5,
        "kind": "number",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "unit": "percent",
        "group": COLOURS,
        "label": "Wallpaper tint",
    },
    "contrast": _choice(
        "normal",
        {"normal": "Normal", "high": "High"},
        group=COLOURS,
        label="Text contrast",
    ),
    "font": _choice(
        "system",
        {
            "system": "Each app's own",
            "rubik": "Rubik",
            "inter": "Inter",
            "serif": "Serif",
            "mono": "Monospace",
        },
        group=COLOURS,
        label="Font",
    ),
    "ui_scale": {
        "default": 1.0,
        "kind": "number",
        "min": 0.8,
        "max": 1.5,
        "step": 0.05,
        "unit": "times",
        "group": SIZE,
        "label": "App size",
        "help": "Everything in Forge, hub and apps, laid out bigger or smaller.",
        # Zooms the page that holds the slider: applied once, on release
        # (a slider applied mid-drag moved under the pointer and flip-flopped).
        "apply": "release",
    },
    "text_scale": {
        "default": 1.0,
        "kind": "number",
        "min": 0.8,
        "max": 1.5,
        "step": 0.05,
        "unit": "times",
        "group": SIZE,
        "label": "Text size",
        "help": "Only the letters, in the hub and every app; boxes stay as they are.",
        # While dragged only the Settings page itself shows the new size.
        "apply": "release",
        "preview": "text_size",
    },
    "icon_scale": {
        "default": 1.0,
        "kind": "number",
        "min": 0.8,
        "max": 1.6,
        "step": 0.05,
        "unit": "times",
        "group": SIZE,
        "label": "Icon size",
        "help": "App icons on the home screen, the bar and the dock.",
        "apply": "release",
    },
    # Overrides for Forge's own keys, {action: "Alt+X, F11"}; "" = default.
    "hub_keys": {
        "default": {},
        "kind": "keys",
        "custom": True,
        "group": KEYS,
        "defaults": HUB_KEYS,
        "labels": HUB_KEY_LABELS,
    },
    # Saved custom size presets, [{name, ui_scale, text_scale, icon_scale}].
    # Drawn by the Settings app as a presets slider in the Size card.
    "size_presets": {
        "default": [],
        "kind": "presets",
        "custom": True,
        "group": SIZE,
        "sets": list(SIZE_KEYS),
    },
    # --- glass and layout
    "look": _choice(
        "classic",
        {k: v["label"] for k, v in list_looks().items()},
        group=LAYOUT,
        label="Look",
        help="The style of the whole hub: "
        + "; ".join(f"{v['label']}: {v['about']}" for v in list_looks().values())
        + ". Classic follows the corner, gap and border settings below; the other looks bring their own.",
    ),
    "icon_style": _choice(
        "new",
        {"new": "New", "classic": "Classic"},
        group=LAYOUT,
        label="Icons",
        help="How the app icons look on the home screen, the bar, the dock and the overview: "
        "New: drawn icons in one style, tinted with each app's colour; "
        "Classic: the emoji Forge had before them. Switches at once.",
    ),
    "glass_style": _choice(
        "flat",
        {k: v["label"] for k, v in list_glass().items()},
        group=LAYOUT,
        label="Glass",
        help="How the bar, dock, popups, app tiles, widgets and the frame round an open app are made: "
        + "; ".join(f"{v['label']}: {v['about']}" for v in list_glass().values())
        + " Frosted and Liquid blur what is behind them live, which costs a little battery; Speed and detail's lite mode turns the blur off.",
    ),
    "glass_strength": {
        "default": 0.6,
        "kind": "number",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "unit": "percent",
        "group": LAYOUT,
        "label": "Glass strength",
        "help": "How much Frosted or Liquid glass blurs, how clear the panes are and how bright their edges catch the light.",
    },
    "glass": {
        "default": 0.62,
        "kind": "number",
        "min": 0.2,
        "max": 1.0,
        "step": 0.02,
        "unit": "solid",
        "group": LAYOUT,
        "label": "App background",
    },
    "dim": {
        "default": 0.18,
        "kind": "number",
        "min": 0.0,
        "max": 0.7,
        "step": 0.02,
        "unit": "percent",
        "group": LAYOUT,
        "label": "Darken the wallpaper",
    },
    "gaps": {
        "default": 10,
        "kind": "int",
        "min": 0,
        "max": 32,
        "unit": "px",
        "group": LAYOUT,
        "label": "Gaps",
    },
    "radius": {
        "default": 14,
        "kind": "int",
        "min": 0,
        "max": 28,
        "unit": "px",
        "group": LAYOUT,
        "label": "Rounded corners",
    },
    "border": _choice(
        "gradient",
        {
            "gradient": "Gradient",
            "accent": "Accent colour",
            "subtle": "Subtle",
            "none": "None",
        },
        group=LAYOUT,
        label="Window border",
    ),
    "behind_apps": _choice(
        "blurred",
        {"blurred": "Blurred", "sharp": "Sharp", "plain": "Plain dark"},
        group=LAYOUT,
        label="Wallpaper behind apps",
    ),
    "shadows": {"default": True, "kind": "bool", "group": LAYOUT, "label": "Shadows"},
    "animations": {
        "default": True,
        "kind": "bool",
        "group": LAYOUT,
        "label": "Animations",
    },
    "anim_speed": {
        "default": 240,
        "kind": "int",
        "min": 0,
        "max": 600,
        "step": 20,
        "unit": "ms",
        "group": LAYOUT,
        "label": "Animation length",
    },
    # --- top bar and dock
    "clock_24h": {
        "default": True,
        "kind": "bool",
        "group": BAR,
        "label": "24-hour clock",
    },
    "clock_seconds": {
        "default": False,
        "kind": "bool",
        "group": BAR,
        "label": "Seconds in the bar",
    },
    "bar_date": {
        "default": False,
        "kind": "bool",
        "group": BAR,
        "label": "Date in the bar",
    },
    "show_stats": {
        "default": True,
        "kind": "bool",
        "group": BAR,
        "label": "CPU, memory and battery",
    },
    # One bar is enough by default: the top bar. The dock waits at the bottom
    # edge and shows on hover; the other two are for anyone who wants it back.
    "dock": _choice(
        "hidden",
        {
            "hidden": "Only when the mouse touches the bottom edge",
            "auto": "On the home screen, hidden in apps",
            "always": "Always show",
            "off": "Off: never show the dock",
        },
        group=BAR,
        label="Dock",
    ),
    "dock_size": {
        "default": 46,
        "kind": "int",
        "min": 36,
        "max": 64,
        "step": 2,
        "unit": "px",
        "group": BAR,
        "label": "Dock icon size",
    },
    # --- home screen
    "home_clock": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "Big clock",
    },
    "greeting": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "Greeting",
    },
    "tile_width": {
        "default": 236,
        "kind": "int",
        "min": 180,
        "max": 360,
        "step": 4,
        "unit": "px",
        "group": HOME_SCREEN,
        "label": "Tile width",
    },
    "open_on": _choice(
        "home",
        {"home": "the home screen", "last": "the last app I used"},
        group=HOME_SCREEN,
        label="Open Forge on",
    ),
    "start_apps": _choice(
        "demand",
        {
            "demand": "the first time I open each one",
            "launch": "all together when Forge opens",
        },
        group=HOME_SCREEN,
        label="Start apps",
        help="On demand makes Forge open lighter; an app's first open waits under a second.",
    ),
    "particles": _choice(
        "soft",
        PARTICLE_LABELS,
        group=HOME_SCREEN,
        label="Particles",
        help="Lights and weather over the wallpaper on the home screen. Match the wallpaper picks one from the picture's name, then its colours (petals for pink blossom, snow for pale winter, rain for dark blue, fireflies for green, dust or light rays for warm pictures). They follow the pointer a little, and a click throws sparks. Drawn on the home screen, and behind open apps unless that is turned off.",
    ),
    "particles_shown": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "Show particles",
        "help": "Alt+P turns them off and on without losing the kind picked above. Alt+E turns the visualiser off, then the particles, then both back on.",
    },
    "particles_in_apps": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "Particles behind open apps",
        "help": "Keep them drawing behind an open app, where the wallpaper shows around it and through it. Off stops them while an app is open, which saves a little power.",
    },
    "desktop_particles": {
        "default": False,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "Particles on the desktop",
        "help": "The same particles on your normal desktop, behind every window; clicks go straight through. On GNOME this needs the Forge desktop particles extension and one log out and back in; on Hyprland also turn on On the desktop > On Hyprland too.",
    },
    # the desktop's own particles page (the owner, 2026-09-27: "the extension
    # for the particles on the desktop should also have settings")
    "desktop_particle_kind": _choice(
        "same",
        {
            "same": "Same as the home screen",
            **{k: v for k, v in PARTICLE_LABELS.items() if k != "off"},
        },
        group=DESKTOP_PARTICLES,
        label="Particles on the desktop",
        help="Which particles the normal desktop shows, when Home screen > Particles on the desktop is on.",
    ),
    "desktop_particle_amount": {
        "default": 1.0,
        "kind": "number",
        "min": 0.25,
        "max": 2.5,
        "step": 0.25,
        "unit": "times",
        "group": DESKTOP_PARTICLES,
        "also": [SPEED],
        "label": "Amount",
        "help": "How many there are on the desktop. Fewer use less power.",
    },
    "desktop_particle_fps": {
        "default": 30,
        "kind": "int",
        "min": 1,
        "max": 60,
        "step": 1,
        "unit": "fps",
        "group": DESKTOP_PARTICLES,
        "also": [SPEED],
        "label": "Frame rate",
        "help": "How many times a second they move. 30 looks smooth and uses about half the power of 60; the frame rate cap under Speed and detail still applies when it is lower.",
    },
    "desktop_particles_rest": {
        "default": True,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "Only while the desktop shows",
        "help": "They stop a few seconds after windows cover the whole desktop, and start again as soon as any of it shows. Off keeps them moving all the time.",
    },
    "desktop_all_screens": {
        "default": True,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "On every screen",
        "help": "With more than one screen plugged in, each one shows the particles, the visualiser and its own widgets behind its windows, so a screen Forge is not on is a scenery screen to pop apps onto (right-click an app > Pop out). Off keeps them to the main screen.",
    },
    "desktop_widgets": {
        "default": False,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "Widgets on the desktop",
        "help": "Your widgets on the normal desktop, behind every window, with their own layout. The Widgets button in the corner of the desktop adds, moves and removes them. Runs without Forge open.",
    },
    "desktop_edit_button": {
        "default": True,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "Widgets button in the corner",
        "help": "A small button at the bottom right of the desktop that starts editing the desktop's widgets. Off hides it; the extension's top bar menu can still start editing.",
    },
    "desktop_panel_icon": {
        "default": True,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "Button in the top bar",
        "help": "The Forge extension's button beside the clock on GNOME: switches for the desktop's particles, widgets and visualiser, Edit desktop widgets, and these settings.",
    },
    # the owner, 2026-09-28: Super's overview made the desktop vanish
    "desktop_in_overview": {
        "default": True,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "Keep them in the overview",
        "help": "When Super zooms out to show your desktops, the particles, widgets and visualiser zoom out with each desktop instead of vanishing. GNOME only.",
    },
    # the owner, 2026-09-28: on GNOME and Hyprland, off by default on Hyprland
    "desktop_on_hyprland": {
        "default": False,
        "kind": "bool",
        "group": DESKTOP_PARTICLES,
        "label": "On Hyprland too",
        "help": "Shows the desktop's particles, widgets and visualiser when you log in to Hyprland as well. Off by default; they start within a few seconds of turning this on.",
    },
    "desktop_widget_scale": {
        "default": 1.0,
        "kind": "number",
        "min": 0.75,
        "max": 1.5,
        "step": 0.05,
        "unit": "times",
        "group": DESKTOP_PARTICLES,
        "label": "Desktop widget size",
    },
    "desktop_visualiser": _choice(
        "off",
        {"off": "Off", **VISUALISER_LABELS},
        group=DESKTOP_PARTICLES,
        label="Visualiser on the desktop",
        help="Draws whatever the laptop is playing on the normal desktop, behind every window. Reads the sound with cava only while something draws.",
    ),
    "desktop_visualiser_size": {
        "default": 1.0,
        "kind": "number",
        "min": 0.5,
        "max": 2.0,
        "step": 0.1,
        "unit": "times",
        "group": DESKTOP_PARTICLES,
        "label": "Desktop visualiser size",
    },
    "desktop_visualiser_fps": {
        "default": 30,
        "kind": "int",
        "min": 10,
        "max": 60,
        "step": 5,
        "unit": "fps",
        "group": DESKTOP_PARTICLES,
        "also": [SPEED],
        "label": "Desktop visualiser frame rate",
    },
    "wall_parallax": {
        "default": 1.0,
        "kind": "number",
        "min": 0,
        "max": 5,
        "step": 0.5,
        "unit": "%",
        "group": HOME_SCREEN,
        "label": "Wallpaper follows the pointer",
        "help": "How far the picture shifts against the pointer, as a share of the screen. 0 keeps it still; it also stays still when animations are off.",
    },
    "wall_3d": {
        "default": False,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "2.5D wallpaper",
        "help": "The character (or, in a landscape, the nearest part of it) is cut out of the picture once, in the background, and floats in front: it moves further than the sky as the pointer moves, the particles fly between them, and it pumps on the beat in Alt+V. Uses the models PanelForge already has; about 8 seconds of CPU per new wallpaper.",
    },
    "wall_3d_depth": {
        "default": 1.0,
        "kind": "number",
        "min": 0.0,
        "max": 3.0,
        "step": 0.1,
        "unit": "times",
        "group": HOME_SCREEN,
        "label": "2.5D depth",
        "help": "How much further the cut-out moves than the picture behind it.",
    },
    "wall_3d_beat": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "2.5D moves to the beat (Alt+V)",
    },
    "wall_3d_mode": _choice(
        "depth",
        {"depth": "Real depth (3D)", "layers": "Two layers"},
        group=HOME_SCREEN,
        label="2.5D style",
        help="Real depth bends the whole picture by its depth map on the graphics chip, so every part moves by how near it is, like a 3D photo, with the cut-out and the particles in front. Two layers moves only the cut-out against the rest, which costs almost nothing.",
    ),
    "wall_3d_drift": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "2.5D drifts on its own",
        "help": "The view sways slowly round the picture even when the pointer is still. Stops on battery and while the GPU is busy.",
    },
    "wall_tint": {
        "default": True,
        "kind": "bool",
        "group": HOME_SCREEN,
        "label": "Time-of-day tint",
        "help": "A light colour over the wallpaper that follows the clock: rose at dawn, clear by day, gold in the early evening, violet at dusk, blue at night.",
    },
    "wall_tint_strength": {
        "default": 1.0,
        "kind": "number",
        "min": 0,
        "max": 2.0,
        "step": 0.1,
        "unit": "times",
        "group": HOME_SCREEN,
        "label": "Tint strength",
        "help": "How strong the time-of-day tint is: 1 is the usual, 0 none, 2 twice as strong.",
    },
    "particle_amount": {
        "default": 1.0,
        "kind": "number",
        "min": 0.25,
        "max": 2.5,
        "step": 0.25,
        "unit": "times",
        "group": HOME_SCREEN,
        "also": [SPEED],
        "label": "Particle amount",
    },
    # more sliders (the owner, 2026-09-27: "basically everything have a slider
    # like fps and level of power used for particles"); defaults keep the look
    "particle_size": {
        "default": 1.0,
        "kind": "number",
        "min": 0.5,
        "max": 2.5,
        "step": 0.25,
        "unit": "times",
        "group": HOME_SCREEN,
        "label": "Particle size",
    },
    "particle_speed": {
        "default": 1.0,
        "kind": "number",
        "min": 0.25,
        "max": 3.0,
        "step": 0.25,
        "unit": "times",
        "group": HOME_SCREEN,
        "label": "Particle speed",
    },
    "particle_fps": {
        "default": 30,
        "kind": "int",
        "min": 5,
        "max": 60,
        "step": 5,
        "unit": "fps",
        "group": HOME_SCREEN,
        "also": [SPEED],
        "label": "Particle frame rate",
        "help": "How often the particles are drawn. Lower uses less power and moves them more slowly; 60 is the full rate. The frame rate cap under Speed and detail applies on top.",
    },
    # home widgets and Relax (B20, 2026-09-27; shared/forge_widgets.js). The
    # layout itself (pages, places) is edited on the home screen with Edit.
    "widgets": {
        "default": True,
        "kind": "bool",
        "group": WIDGETS,
        "label": "Widgets on the home screen",
        "help": "Cards above the app tiles (clock, weather, music, calendar, system, disk, network, Jan, alarms, notes, visualiser, relax) on pages you can swipe between. The Edit button on the home screen moves, resizes, adds and removes them, and each card's ⚙ changes what it shows and its colour.",
    },
    "widget_look": _choice(
        "material",
        {"material": "Material (tonal cards)", "glass": "Glass, like the tiles"},
        group=WIDGETS,
        label="Widget look",
    ),
    "widget_scale": {
        "default": 1.0,
        "kind": "number",
        "min": 0.75,
        "max": 1.5,
        "step": 0.05,
        "unit": "times",
        "group": WIDGETS,
        "label": "Widget size",
    },
    "weather_units": _choice(
        "c",
        {"c": "Celsius, km/h", "f": "Fahrenheit, mph"},
        group=WIDGETS,
        label="Weather units",
    ),
    "alarm_sound": _choice(
        "chime",
        {"chime": "Soft chime", "bell": "Bell", "beep": "Beeps"},
        group=WIDGETS,
        label="Alarm sound",
        help="What the Alarms widget plays when an alarm or a timer goes off (it also shows a note on screen).",
    ),
    "breath_seconds": {
        "default": 4,
        "kind": "int",
        "min": 2,
        "max": 8,
        "step": 1,
        "unit": "s",
        "group": WIDGETS,
        "label": "Breathing pace",
        "help": "Relax (the moon widget) breathes in, holds, breathes out and holds for this long each.",
    },
    "visualiser_shown": {
        "default": True,
        "kind": "bool",
        "group": MUSIC,
        "label": "Show the visualiser",
        "help": "Alt+M turns it off and on without losing the style picked below (in Alt+V it opens the visuals settings instead).",
    },
    "visualiser_gain": {
        "default": 1.0,
        "kind": "number",
        "min": 0.5,
        "max": 2.5,
        "step": 0.1,
        "unit": "times",
        "group": MUSIC,
        "label": "Visualiser sensitivity",
        "help": "How strongly it reacts to the sound: higher makes quiet music move it more. Terrain already reacts 20% more than the rest.",
    },
    "visualiser_opacity": {
        "default": 1.0,
        "kind": "number",
        "min": 0.2,
        "max": 1.0,
        "step": 0.05,
        "unit": "percent",
        "group": MUSIC,
        "label": "Visualiser opacity",
        "help": "Lower lets more of the wallpaper show through it.",
    },
    "visualiser_colours": _choice(
        "accent",
        {
            "accent": "The wallpaper's colours",
            "rainbow": "Rainbow, slowly turning",
            "neon": "Neon pink and cyan",
            "fire": "Fire",
            "ice": "Ice",
            "white": "White",
        },
        group=MUSIC,
        label="Visualiser colours",
    ),
    "visualiser_in_apps": {
        "default": False,
        "kind": "bool",
        "group": MUSIC,
        "label": "Visualiser behind open apps",
        "help": "Keep the visualiser drawing behind an open app, where the wallpaper shows around it and through it, like the particles can. Off hides it while an app is open, which saves power. The frame rate cap under Speed and detail still applies.",
    },
    "visualiser": _choice(
        "off",
        {"off": "Off", **VISUALISER_LABELS},
        group=MUSIC,
        label="Music visualiser",
        help="Draws the music on the home screen: bars along the bottom, a ring around the clock, a glowing wave, mirrored bars, aurora bands across the top, orbiting lights that burst on the beat, or a glowing 3D landscape rolling towards you.",
    ),
    "visualiser_source": _choice(
        "system",
        {
            "system": "Anything playing on the laptop",
            "music": "Only Forge's background music",
        },
        group=MUSIC,
        label="Visualiser listens to",
        help="Anything playing: QBZ, a browser tab, a Forge app or the background music (read with cava, only while the visualiser is on screen). Only Forge's background music: needs Background music on.",
    ),
    "visualiser_size": {
        "default": 1.0,
        "kind": "number",
        "min": 0.5,
        "max": 2.0,
        "step": 0.1,
        "unit": "times",
        "group": MUSIC,
        "label": "Visualiser size",
    },
    "music": {
        "default": False,
        "kind": "bool",
        "group": MUSIC,
        "label": "Background music",
        "help": "Plays the music folder below on the home screen and in ordinary apps, and fades out inside games and players. The note button in the top bar pauses it.",
    },
    "music_folder": {
        "default": "~/Music",
        "kind": "text",
        "group": MUSIC,
        "label": "Music folder",
        "help": "Its music and videos play, and those in folders one level down (Media saves downloads to ~/Music/Forge). The note button's menu adds a YouTube link's audio here.",
    },
    "music_volume": {
        "default": 35,
        "kind": "int",
        "min": 0,
        "max": 200,
        "step": 5,
        "unit": "whole_percent",
        "group": MUSIC,
        "label": "Music volume",
        "help": "Past 100% the music is boosted, for quiet tracks. The music button's Now playing has the same slider.",
    },
    "music_level": {
        "default": True,
        "kind": "bool",
        "group": MUSIC,
        "label": "Even out loud and quiet tracks",
        "help": "Quiet songs are lifted and loud ones held back, so one volume suits the whole folder.",
    },
    "music_duck": {
        "default": True,
        "kind": "bool",
        "group": MUSIC,
        "label": "Step aside for other sound",
        "help": "The background music fades out while a video or song plays in another Forge app, and comes back after.",
    },
    "music_fade": {
        "default": 150,
        "kind": "int",
        "min": 0,
        "max": 2000,
        "step": 50,
        "unit": "ms",
        "group": MUSIC,
        "label": "Music fade",
        "help": "How long the background music takes to fade out when you pause it or it steps aside, and to fade back in. 0 stops it at once.",
    },
    # --- zen mode: what its screen shows (Alt+Z or the moon on the bar)
    # Background mode (Alt+B): zen without the clock or timer, only the
    # wallpaper, its particles and the visualiser (the owner, 2026-09-27).
    "ambient_particles": {
        "default": 1.5,
        "kind": "number",
        "min": 0.5,
        "max": 3.0,
        "step": 0.25,
        "unit": "times",
        "group": AMBIENT,
        "label": "Particle boost",
        "help": "How many more particles than on the home screen.",
    },
    "ambient_visualiser": _choice(
        "keep",
        {"keep": "Same as the home screen", "off": "Off", **VISUALISER_LABELS},
        group=AMBIENT,
        label="Visualiser",
        popup_label="Visualiser in background mode",
    ),
    "ambient_drift": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Slow wallpaper drift",
        "help": "The picture pans and zooms very slowly, like a film still.",
    },
    "ambient_drift_seconds": {
        "default": 90,
        "kind": "int",
        "min": 20,
        "max": 300,
        "step": 10,
        "unit": "s",
        "group": AMBIENT,
        "label": "Drift length",
        "help": "How long one slow sweep of the wallpaper takes. Shorter is faster.",
    },
    "ambient_hint": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Say how to leave",
        "help": "A line at the bottom for a few seconds: Esc or Alt+B to come back.",
    },
    # B40: the entrance, and the Alt+V pulse (Pulse in hub/index.html)
    "ambient_entrance": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Entrance animation",
        "help": "A bloom of light over the picture as background mode starts.",
    },
    "pulse_strength": {
        "default": 1.0,
        "kind": "number",
        "min": 0.25,
        "max": 2.0,
        "step": 0.25,
        "unit": "times",
        "group": AMBIENT,
        "label": "Pulse strength (Alt+V)",
        "help": "How hard the wallpaper, the edge glow and the light swell on the beat in the Alt+V pulse.",
    },
    "pulse_glow": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Edge glow (Alt+V)",
        "help": "The screen's edges glow in the accent colours on the beat.",
    },
    "pulse_sway": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Sway (Alt+V)",
        "help": "The picture sways a little, more on the beat.",
    },
    # The Alt+V effects, one switch each (the owner, 2026-09-27: "way better")
    "pulse_punch": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Beat punch (Alt+V)",
        "help": "The picture kicks in sharply on each bass hit and eases back.",
    },
    "pulse_flash": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Colour flash (Alt+V)",
        "help": "A flash of the accent colours over the picture on a strong beat.",
    },
    "pulse_rings": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Shockwave rings (Alt+V)",
        "help": "A ring of light spreads out from the middle on each beat.",
    },
    "pulse_beams": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Stage lights (Alt+V)",
        "help": "Beams of light sweep up from the bottom of the screen, faster and brighter as the music gets louder.",
    },
    "pulse_burst": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Particle bursts (Alt+V)",
        "help": "The particles jump forward on each beat, and big beats throw sparks.",
    },
    "pulse_chroma": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Colour split (Alt+V)",
        "help": "On a big beat the picture splits into red and blue edges for a moment, like a music video.",
    },
    "pulse_shake": {
        "default": False,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Screen shake (Alt+V)",
        "help": "The picture shakes a little on the hardest beats.",
    },
    "pulse_sensitivity": {
        "default": 1.0,
        "kind": "number",
        "min": 0.5,
        "max": 2.0,
        "step": 0.1,
        "unit": "times",
        "group": AMBIENT,
        "label": "Beat sensitivity (Alt+V)",
        "help": "Higher catches softer beats; lower waits for the big ones.",
    },
    "fx_hud": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "label": "Show the Alt+V controls",
        "help": "A small line of what the keys do when the pulse starts, and what changed when you press one: arrows change the visualiser and the pulse strength, Shift and the arrows the particles, 1 to 8 flip the beat effects, Alt+M opens the visuals settings.",
    },
    "pulse_full_fps": {
        "default": True,
        "kind": "bool",
        "group": AMBIENT,
        "also": [SPEED],
        "label": "Full frame rate (Alt+V)",
        "help": "While the pulse runs it ignores the frame rate cap, so beats land on time. Off keeps the cap, which saves power.",
    },
    "zen_clock": {
        "default": True,
        "kind": "bool",
        "group": ZEN,
        "label": "Clock and date",
    },
    "zen_intention": {
        "default": True,
        "kind": "bool",
        "group": ZEN,
        "label": "One-line intention",
    },
    "zen_timer": {
        "default": True,
        "kind": "bool",
        "group": ZEN,
        "label": "Focus timer",
    },
    "zen_music": {
        "default": True,
        "kind": "bool",
        "group": ZEN,
        "label": "Music button",
        "help": "The music button shows only when background music is on.",
    },
    "zen_effects": {
        "default": True,
        "kind": "bool",
        "group": ZEN,
        "label": "Wallpaper effects and visualiser",
        "help": "Off leaves only the still wallpaper behind the zen screen.",
    },
    # --- starting and closing
    "start_at_login": {
        "default": True,
        "kind": "bool",
        "group": STARTUP,
        "label": "Start Forge when I log in",
        "help": "Forge starts hidden and waits, so opening it later is instant.",
    },
    "close_action": _choice(
        "background",
        {
            "background": "keeps Forge ready in the background",
            "quit": "quits Forge and every app",
        },
        group=STARTUP,
        label="Closing the window",
        help="In the background your apps and music keep going. Quit from the power button on the bar, or Ctrl+Shift+Q.",
    ),
    # --- speed
    "pause_when_hidden": {
        "default": True,
        "kind": "bool",
        "group": SPEED,
        "label": "Rest while Forge is hidden",
        "help": "Minimised or on another workspace, Forge stops its animations and visualiser a few seconds later; music keeps playing. On another monitor it keeps going.",
    },
    "max_fps": {
        "default": 60,
        "kind": "int",
        "min": 1,
        "max": 60,
        "group": SPEED,
        "label": "Frame rate cap",
        "help": "Particles, the visualiser, games and the Browser's web pages draw at most this many frames a second. Lower saves power; 60 means no cap. Also in the Power app.",
    },
    "battery_fps": {
        "default": 30,
        "kind": "int",
        "min": 1,
        "max": 60,
        "group": SPEED,
        "label": "Frame rate on battery",
        "help": "The frame rate cap while the laptop runs on battery; plugged in, the cap above applies. Particles keep moving, in fewer frames. 60 means the same as plugged in. Also in the Power app.",
    },
    "battery_particles": {
        "default": 0.5,
        "kind": "number",
        "min": 0,
        "max": 1,
        "step": 0.05,
        "unit": "percent",
        "group": SPEED,
        "label": "Particles on battery",
        "help": "The share of the particles kept while unplugged, on the home screen and the desktop. 100% keeps them all; 0% turns them off until the charger is back.",
    },
    "battery_particle_fps": {
        "default": 20,
        "kind": "int",
        "min": 20,
        "max": 60,
        "step": 1,
        "unit": "fps",
        "group": SPEED,
        "label": "Particle frame rate on battery",
        "help": "How often the particles move while unplugged, on the home screen and the desktop. Never under 20, so they still look like they are flying.",
    },
    "battery_visualiser": {
        "default": False,
        "kind": "bool",
        "group": SPEED,
        "label": "Visualiser on battery",
        "help": "Off turns the music visualiser off while unplugged, on the home screen and the desktop, and back on with the charger. Alt+V still shows it.",
    },
    "battery_saver": {
        "default": 20,
        "kind": "int",
        "min": 0,
        "max": 50,
        "step": 5,
        "unit": "%",
        "group": SPEED,
        "label": "Low battery saver",
        "help": "Unplugged and at or under this charge, Forge and the desktop ease all the way off: no visualiser, half the particles, 25 frames a second. 0 turns it off.",
    },
    "gpu_ease": {
        "default": True,
        "kind": "bool",
        "group": SPEED,
        "label": "Ease off when the GPU is busy",
        "help": "While a game, a local AI model, a render or anything else keeps the graphics chip busy, Forge and the desktop step down so both stay smooth: first the visualiser goes off, then half the particles, then everything drops to 25 frames a second (particles never under 20). It all comes back about 10 seconds after the load ends. Alt+V ignores it.",
    },
    "gpu_ease_note": {
        "default": True,
        "kind": "bool",
        "group": SPEED,
        "label": "Say when it eases off",
        "help": 'A short line at the top of the home screen, such as "Easing off while Blender uses the GPU: visualiser off".',
    },
    "video_fps": {
        "default": 60,
        "kind": "int",
        "min": 30,
        "max": 60,
        "step": 30,
        "unit": "fps",
        "group": SPEED,
        "label": "Browser video frame rate",
        "help": "The most frames a second a video in the Browser app may play at, YouTube included. 30 picks the 30 fps version of a 60 fps video, about half the decoding work. Takes effect from the next video.",
    },
    "battery_video_fps": {
        "default": 30,
        "kind": "int",
        "min": 30,
        "max": 60,
        "step": 30,
        "unit": "fps",
        "group": SPEED,
        "label": "Browser video frame rate on battery",
    },
    "video_height": _choice(
        "2160",
        VIDEO_HEIGHTS,
        group=SPEED,
        label="Browser video quality",
        help="The sharpest video a page in the Browser app may pick, YouTube included. Lower uses less power and data; the player can still go lower on its own. Takes effect from the next video.",
    ),
    "battery_video_height": _choice(
        "1080",
        VIDEO_HEIGHTS,
        group=SPEED,
        label="Browser video quality on battery",
    ),
    "smooth_scroll": {
        "default": True,
        "kind": "bool",
        "group": SPEED,
        "label": "Smooth scrolling",
        "help": "Pages glide when scrolled, in every Forge app and the Browser. Off jumps straight there, which draws far fewer frames.",
    },
    "battery_smooth_scroll": {
        "default": True,
        "kind": "bool",
        "group": SPEED,
        "label": "Smooth scrolling on battery",
    },
    "lite_effects": _choice(
        "full",
        {"full": "Full", "battery": "On battery", "always": "Always"},
        group=SPEED,
        label="Lighter effects",
        help="Looping animations play once and stop, and the frosted blur behind panels goes flat. Particles, the wallpaper and the visualiser stay. Full keeps every effect; On battery lightens only while unplugged.",
    ),
    # --- apps: tiles taken off the home screen (app or group:<id> keys).
    # Hidden ones stay in search and All apps; archived ones leave both and
    # their backends never start. Drawn by the Settings app's Apps card.
    "hidden_apps": {"default": [], "kind": "names", "custom": True, "group": APPS},
    "archived_apps": {"default": [], "kind": "names", "custom": True, "group": APPS},
    # Folders made by dragging one app onto another on the home screen:
    # {id: {"label": ..., "members": [app, ...]}}. The hub edits it.
    "app_folders": {"default": {}, "kind": "folders", "custom": True},
    "claude_resume": {
        "default": True,
        "kind": "bool",
        "group": APPS,
        "label": "Claude: carry on after a restart",
        "help": "A conversation cut off mid-task by Forge quitting or the laptop restarting carries on by itself the next time Forge starts, one every 20 seconds. Off, it waits for your next message. Stop still stops it for good.",
    },
    "claude_archive_hours": {
        "default": 2,
        "kind": "int",
        "min": 0,
        "max": 168,
        "group": APPS,
        "label": "Claude: archive quiet conversations after (hours)",
        "help": "In the Claude app, a conversation in no folder moves to Archived once it has been quiet this many hours. 0 never archives. Sending it a message, filing it in a folder or its restore button brings it back.",
    },
    # --- full screen
    "start_fullscreen": {
        "default": True,
        "kind": "bool",
        "group": FULL,
        "label": "Start full screen",
        "help": "Takes effect the next time Forge starts. F11 still switches it.",
    },
    "auto_fullscreen": _choice(
        "smart",
        {
            "smart": "Smart: games and players",
            "always": "Every app",
            "off": "Never",
        },
        group=FULL,
        label="Apps fill the screen",
        help="Smart uses each app's own manifest: apps marked fullscreen auto.",
    ),
    "reveal_bar": {
        "default": True,
        "kind": "bool",
        "group": FULL,
        "label": "Top edge shows the bar",
        "help": "Touch the top of the screen to see the apps, their numbers and the clock.",
    },
    "reveal_delay": {
        "default": 120,
        "kind": "int",
        "min": 0,
        "max": 800,
        "step": 20,
        "unit": "ms",
        "group": FULL,
        "label": "Top edge delay",
    },
}

# Font stacks for the `font` setting. "" leaves each app's own font alone.
FONTS = {
    "system": "",
    "rubik": '"Rubik", "Inter", system-ui, sans-serif',
    "inter": '"Inter", system-ui, sans-serif',
    "serif": '"Noto Serif", Georgia, serif',
    "mono": '"JetBrains Mono", ui-monospace, monospace',
}

# The palette in shared/forge.css. "Plain dark grey" is exactly this.
NEUTRAL_SURFACES = {
    "bg": "#0f1115",
    "panel": "#171a21",
    "panel_2": "#1e222b",
    "border": "#2a2f3a",
    "text": "#e7e9ee",
    "muted": "#8b93a3",
}


# --- settings ---------------------------------------------------------------


def _clean(key: str, value):
    spec = SETTINGS[key]
    kind = spec["kind"]
    try:
        if kind == "bool":
            return bool(value)
        if kind in ("number", "int"):
            number = float(value)
            number = max(spec["min"], min(spec["max"], number))
            return int(round(number)) if kind == "int" else round(number, 3)
        if kind == "choice":
            return value if value in spec["choices"] else spec["default"]
        if kind == "presets":
            return _clean_presets(value)
        if kind == "names":
            return _clean_names(value)
        if kind == "folders":
            return _clean_folders(value)
        if kind == "keys":
            return {
                action: str(text).strip()[:40]
                for action, text in (value if isinstance(value, dict) else {}).items()
                if action in HUB_KEYS and str(text or "").strip()
            }
        if kind == "colour":
            text = str(value or "").strip()
            if text == "":
                return ""
            if len(text) == 7 and text[0] == "#":
                int(text[1:], 16)
                return text.lower()
            return spec["default"]
        return str(value or "")
    except (TypeError, ValueError):
        return spec["default"]


def _clean_names(value) -> list[str]:
    """App names: short, plain characters, no repeats, at most 300."""
    names: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = item if isinstance(item, str) else ""
        if text and len(text) <= 64 and text not in names:
            if all(c.isalnum() or c in "_.:-" for c in text):
                names.append(text)
    return names[:300]


def _clean_folders(value) -> dict:
    """App folders: each needs a short id and two or more apps, and an app
    sits in one folder only (the first that names it)."""
    out: dict = {}
    taken: set = set()
    for fid, item in (value if isinstance(value, dict) else {}).items():
        if not isinstance(fid, str) or not fid or len(fid) > 24:
            continue
        if not all(c.isalnum() or c in "_-" for c in fid) or not isinstance(item, dict):
            continue
        members = [m for m in _clean_names(item.get("members")) if m not in taken]
        if len(members) < 2:
            continue
        taken.update(members)
        label = str(item.get("label") or "").strip()[:40] or "Folder"
        out[fid] = {"label": label, "members": members}
        if len(out) >= 50:
            break
    return out


def _clean_presets(value) -> list[dict]:
    """Named sets of the three sizes; nameless or malformed ones are dropped."""
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:30]
        if not name:
            continue
        preset: dict = {"name": name}
        for key in SIZE_KEYS:
            preset[key] = _clean(key, item.get(key, SETTINGS[key]["default"]))
        out.append(preset)
    return out[:12]


def size_presets(settings: dict) -> list[dict]:
    """The built-in size presets, then the user's saved ones."""
    mine = [dict(p, mine=True) for p in _clean_presets(settings.get("size_presets"))]
    return SIZE_PRESETS + mine


def load(path: Path | None = None) -> dict:
    """The user's settings, with defaults for anything missing or broken."""
    path = path or CONFIG
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        stored = {}
    out = {}
    for key, spec in SETTINGS.items():
        out[key] = _clean(key, stored[key]) if key in stored else spec["default"]
    return out


def save(changes: dict, path: Path | None = None) -> dict:
    """Merge `changes` into the stored settings and return them all, cleaned.
    Unknown keys in `changes` are ignored.

    Only the changed keys are rewritten: everything else stays in the file
    exactly as stored. Each app backend imported this module when it started,
    so an older copy must not reset or drop settings it does not know (that
    is how changing the wallpaper sometimes turned the particles off)."""
    path = path or CONFIG
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    for key, value in (changes or {}).items():
        if key in SETTINGS:
            stored[key] = _clean(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    tmp.replace(path)
    return load(path)


# forge.py runs on the system python (it needs PyGObject), never an app's venv,
# which is what sys.executable is when the Settings app calls this.
AUTOSTART_ENTRY = """[Desktop Entry]
Type=Application
Name=Forge
Comment=Starts Forge hidden at login, so opening it is instant
Exec=/usr/bin/python3 {forge} --background
Path={folder}
Icon={icon}
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
"""


def set_autostart(on: bool, path: Path | None = None) -> None:
    """Start Forge when I log in: an XDG autostart entry (GNOME, and Hyprland
    through its session's autostart) running forge.py --background, or none."""
    path = Path(path or AUTOSTART)
    if not on:
        path.unlink(missing_ok=True)
        return
    text = AUTOSTART_ENTRY.format(
        forge=FORGE_PY,
        folder=FORGE_PY.parent,
        icon=HOME / ".local/share/icons/forge.png",
    )
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# The visuals popup (Alt+Shift+M anywhere, Alt+M inside the Alt+V pulse):
# these settings, in this order. The hub builds its controls from the rows
# theme() hands it, so adding one here is the whole change.
VISUALS_POPUP = [
    "visualiser_shown",
    "visualiser",
    "ambient_visualiser",
    "visualiser_gain",
    "visualiser_size",
    "visualiser_opacity",
    "visualiser_colours",
    "visualiser_source",
    "particles_shown",
    "particles",
    "particle_amount",
    "particle_size",
    "particle_speed",
    "pulse_strength",
    "pulse_sensitivity",
    "pulse_glow",
    "pulse_sway",
    "pulse_punch",
    "pulse_flash",
    "pulse_rings",
    "pulse_beams",
    "pulse_burst",
    "pulse_chroma",
    "pulse_shake",
    "wall_parallax",
    "wall_tint",
    "glass_style",
    "glass_strength",
    "wall_3d",
    "wall_3d_mode",
    "wall_3d_depth",
    "wall_3d_drift",
    "wall_3d_beat",
    "fx_hud",
]


def popup_rows(keys: list[str]) -> list[dict]:
    """The settings' descriptions for a popup outside the Settings app."""
    fields = (
        "kind",
        "default",
        "label",
        "min",
        "max",
        "step",
        "unit",
        "choices",
        "labels",
    )
    # a row may carry a clearer name for the popup, where its page's heading
    # is not there to say which visualiser it is
    return [
        {
            "key": k,
            **{f: SETTINGS[k][f] for f in fields if f in SETTINGS[k]},
            "label": SETTINGS[k].get("popup_label", SETTINGS[k]["label"]),
        }
        for k in keys
    ]


def describe() -> dict:
    """The settings table the Settings app shows, less the ones this system
    cannot use: the portable launcher lists those in FORGE_CUT_SETTINGS."""
    cut = set(filter(None, os.environ.get("FORGE_CUT_SETTINGS", "").split(",")))
    return {key: dict(spec) for key, spec in SETTINGS.items() if key not in cut}


def power_values(settings: dict, on_battery: bool) -> dict:
    """What applies now for each setting in POWER_PAIRS: the plugged-in value,
    lightened by its battery twin while on battery."""
    out = {}
    for key, (twin, rule) in POWER_PAIRS.items():
        value = settings[key]
        if on_battery:
            other = settings[twin]
            if rule == "scale":
                value = round(value * other, 3)
            elif isinstance(value, str):
                value = min(value, other, key=int)
            else:
                value = min(value, other)
        out[key] = value
    return out


# --- wallpapers ---------------------------------------------------------------


def list_wallpapers(directory: Path | None = None) -> list[dict]:
    """Every picture in the folder and its sub-folders, named by its path
    relative to the folder. Hidden folders are skipped: the collection is a
    git clone, and nothing under .git is a wallpaper."""
    directory = directory or WALLPAPER_DIR
    if not directory.is_dir():
        return []
    rows = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            path = Path(root) / name
            if name.startswith(".") or path.suffix.lower() not in IMAGE_TYPES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rows.append({"name": path.relative_to(directory).as_posix(), "bytes": size})
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def wallpaper_path(name: str, directory: Path | None = None) -> Path | None:
    """A wallpaper by file name, only if it really is one in the folder.

    The name is matched against the folder listing rather than joined onto the
    folder path, so `../../.ssh/id_rsa` is simply not a wallpaper.
    """
    directory = directory or WALLPAPER_DIR
    for row in list_wallpapers(directory):
        if row["name"] == name:
            return directory / name
    return None


def _gsettings(key: str) -> str:
    schema = (
        "org.gnome.desktop.interface"
        if key == "color-scheme"
        else "org.gnome.desktop.background"
    )
    try:
        out = subprocess.run(
            ["gsettings", "get", schema, key], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def desktop_wallpaper(state: Path | None = None, gsettings=_gsettings) -> Path | None:
    """The desktop's wallpaper: GNOME's own setting (its dark-mode picture when
    the desktop is dark) on GNOME, else the file caelestia keeps on Hyprland;
    on Windows (a Forge release) the picture Windows' own setting names."""
    if sys.platform == "win32":
        return _windows_wallpaper()
    if "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
        dark = "dark" in gsettings("color-scheme")
        uri = gsettings("picture-uri-dark" if dark else "picture-uri").strip("'\"")
        if uri.startswith("file://"):
            path = Path(unquote(urlsplit(uri).path))
            if path.is_file():
                return path
    state = state or DESKTOP_WALLPAPER
    try:
        path = Path(state.read_text(encoding="utf-8").strip()).expanduser()
    except Exception:
        return None
    return path if path.is_file() else None


def _windows_wallpaper() -> Path | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            value = winreg.QueryValueEx(key, "WallPaper")[0]
    except OSError:
        return None
    path = Path(str(value)) if value else None
    return path if path and path.is_file() else None


def desktop_follow_supported(state: Path | None = None) -> bool:
    """Whether desktop_wallpaper can read this desktop at all: GNOME, the
    caelestia file on Hyprland, or Windows. Elsewhere (macOS, other Linux
    desktops) Settings does not offer "Follow my desktop wallpaper"."""
    return (
        sys.platform == "win32"
        or "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        or (state or DESKTOP_WALLPAPER).is_file()
    )


def chosen_wallpaper(settings: dict, directory: Path | None = None) -> Path | None:
    """The picture to use: the chosen one, else the desktop's, else the first."""
    if settings.get("wallpaper"):
        found = wallpaper_path(settings["wallpaper"], directory)
        if found:
            return found
    desktop = desktop_wallpaper()
    if desktop:
        return desktop
    rows = list_wallpapers(directory)
    return (directory or WALLPAPER_DIR) / rows[0]["name"] if rows else None


# --- rendering ----------------------------------------------------------------

# Thumbnails are generated on demand as the Settings grid scrolls. Two at a
# time keeps a first visit from pinning every CPU core.
_magick_slots = threading.BoundedSemaphore(2)


def _magick() -> str | None:
    return shutil.which("magick")


def _key(source: Path) -> str:
    stat = source.stat()
    raw = f"{source.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _run(args: list[str]) -> bool:
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=60)
        return True
    except Exception:
        return False


def thumbnail(
    source: Path, cache: Path | None = None, background: bool = False
) -> Path | None:
    """A 480x270 preview for the Settings grid, or None without ImageMagick.

    `background` is the warm-up: run at the lowest CPU priority and outside
    the two slots a person scrolling the grid gets.
    """
    cache = cache or CACHE
    out = cache / "thumbs" / f"{_key(source)}.jpg"
    if out.is_file():
        return out
    tool = _magick()
    if not tool:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    # Unique per call: the warm-up and a click can make the same one at once.
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.{threading.get_ident()}.jpg")
    command = [
        tool,
        # Decode a JPEG at reduced size from the start: a big speed-up on 4K
        # sources, and ignored for other formats.
        "-define",
        "jpeg:size=960x540",
        str(source),
        "-thumbnail",
        "480x270^",
        "-gravity",
        "center",
        "-extent",
        "480x270",
        "-strip",
        "-quality",
        "80",
        str(tmp),
    ]
    if background:
        if shutil.which("nice"):
            command = ["nice", "-n", "15", *command]
        ok = _run(command)
    else:
        with _magick_slots:
            ok = _run(command)
    if not ok:
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(out)
    return out


def warm_thumbnails(
    first: list[str] | None = None,
    directory: Path | None = None,
    cache: Path | None = None,
) -> int:
    """Make every wallpaper's thumbnail ahead of time, one at a time, niced.

    Made on demand, a first visit to Settings sat mostly blank: a 4K PNG takes
    around half a second to decode, and the grid asks for twenty at once. The
    Settings backend runs this in a thread when it starts, current wallpaper
    first. Each is made once and cached, so later launches cost nothing.
    Returns how many were made.
    """
    directory = directory or WALLPAPER_DIR
    names = [row["name"] for row in list_wallpapers(directory)]
    wanted = [n for n in (first or []) if n in names]
    wanted += [n for n in names if n not in wanted]
    made = 0
    for name in wanted:
        source = directory / name
        if ((cache or CACHE) / "thumbs" / f"{_key(source)}.jpg").is_file():
            continue
        if thumbnail(source, cache, background=True):
            made += 1
    return made


def parse_histogram(text: str) -> list[tuple[int, str]]:
    """(pixel count, #rrggbb) rows from `magick ... histogram:info:-`."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].endswith(":"):
            continue
        colour = next((p for p in parts if p.startswith("#") and len(p) >= 7), "")
        try:
            rows.append((int(parts[0][:-1]), colour[:7].lower()))
        except ValueError:
            continue
    rows.sort(reverse=True)
    return [r for r in rows if r[1]]


def _hls(colour: str) -> tuple[float, float, float]:
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def _hex(h: float, lightness: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h, lightness, s)
    return "#" + "".join(f"{round(c * 255):02x}" for c in (r, g, b))


def pick_accent(palette: list[str]) -> str:
    """The wallpaper colour that makes the best accent, made legible on dark.

    A photograph's most common colour is usually its darkest, which is useless
    as an accent. The pick favours saturation, weighted a little by how common
    the colour is, then lifts it to a readable lightness on a dark panel.
    """
    best, score = "", -1.0
    for rank, colour in enumerate(palette):
        h, lightness, s = _hls(colour)
        if lightness < 0.08 or lightness > 0.95:
            continue
        weight = s * (1.0 - rank * 0.06)
        if weight > score:
            best, score = colour, weight
    if not best or _hls(best)[2] < 0.08:
        # A grey wallpaper has no hue to lift; lifting one would invent red.
        return FALLBACK_ACCENT
    return lift(best)


def lift(colour: str) -> str:
    """A palette colour at a lightness and saturation that read on dark glass."""
    h, _lightness, s = _hls(colour)
    return _hex(h, 0.68, max(s, 0.45))


def swatches(palette: list[str]) -> list[str]:
    """The wallpaper's colours as accent choices: lifted, greys and repeats out."""
    out: list[str] = []
    for colour in palette:
        if _hls(colour)[2] < 0.08:
            continue
        lifted = lift(colour)
        if lifted not in out:
            out.append(lifted)
    return out


def _variants(accent: str) -> tuple[str, str]:
    """(a darker partner for fills, the text colour that sits on the accent)."""
    h, lightness, s = _hls(accent)
    darker = _hex(h, max(0.0, lightness - 0.1), s)
    r, g, b = (int(accent[i : i + 2], 16) / 255 for i in (1, 3, 5))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return darker, ("#0b1220" if luminance > 0.45 else "#ffffff")


def surfaces(accent: str, tint: float, mode: str, contrast: str) -> dict:
    """The panel, border and text colours every app is drawn with.

    In wallpaper mode they take the accent's hue (the accent is already the
    wallpaper's colour) at fixed dark lightnesses, so text contrast holds
    whatever the wallpaper: tests check 7:1 for text and 4.5:1 for quiet text
    on every panel.
    """
    high = contrast == "high"
    if mode != "wallpaper":
        if not high:
            return dict(NEUTRAL_SURFACES)
        return dict(NEUTRAL_SURFACES, text="#ffffff", muted="#b8c0cc", border="#3e4552")
    h = _hls(accent)[0]
    s = 0.08 + 0.42 * max(0.0, min(1.0, tint))
    return {
        "bg": _hex(h, 0.065, s),
        "panel": _hex(h, 0.105, s),
        "panel_2": _hex(h, 0.135, s),
        "border": _hex(h, 0.30 if high else 0.21, s * 0.8),
        "text": _hex(h, 0.97 if high else 0.93, s * 0.3),
        "muted": _hex(h, 0.80 if high else 0.70, s * 0.35),
    }


def render(source: Path, cache: Path | None = None) -> dict:
    """The wallpaper copy, its blurred copy and its palette - cached by file.

    Without ImageMagick the original file is used as-is and the accent falls
    back to the default; Forge still starts, it just starts heavier.
    """
    cache = cache or CACHE
    key = _key(source)
    folder = cache / "wallpaper"
    image, blur, meta = (
        folder / f"{key}.jpg",
        folder / f"{key}-blur.jpg",
        folder / f"{key}.json",
    )
    if image.is_file() and blur.is_file() and meta.is_file():
        try:
            return {
                "image": image,
                "blur": blur,
                **json.loads(meta.read_text(encoding="utf-8")),
            }
        except Exception:
            pass
    tool = _magick()
    if not tool:
        return {"image": source, "blur": source, "palette": []}
    folder.mkdir(parents=True, exist_ok=True)
    ok = _run(
        [
            tool,
            str(source),
            "-resize",
            "2560x1440^",
            "-gravity",
            "center",
            "-extent",
            "2560x1440",
            "-strip",
            "-quality",
            "85",
            str(image),
        ]
    ) and _run(
        [
            tool,
            str(image),
            "-resize",
            "12.5%",
            "-blur",
            "0x6",
            "-resize",
            "800%",
            "-quality",
            "80",
            str(blur),
        ]
    )
    if not ok:
        return {"image": source, "blur": source, "palette": []}
    try:
        text = subprocess.run(
            [
                tool,
                str(image),
                "-resize",
                "96x96",
                "-colors",
                "8",
                "-format",
                "%c",
                "histogram:info:-",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        palette = [colour for _count, colour in parse_histogram(text)]
    except Exception:
        palette = []
    meta.write_text(json.dumps({"palette": palette}), encoding="utf-8")
    return {"image": image, "blur": blur, "palette": palette}


# --- the theme ------------------------------------------------------------------


# .mp4 and .webm too: a saved YouTube "lofi playlist" video is music here,
# and an audio element plays the sound track of a video file
MUSIC_TYPES = {
    ".mp3",
    ".ogg",
    ".opus",
    ".flac",
    ".m4a",
    ".aac",
    ".wav",
    ".mp4",
    ".webm",
}


def music_tracks(folder: str, limit: int = 300) -> list[dict]:
    """The playable files in the music folder, as file:// links the hub can
    hand to an audio element (the hub itself is loaded over file://, so
    these play with no server). Sorted by name; sub-folders one level."""
    root = Path(os.path.expanduser(folder or ""))
    if not folder or not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*")) + sorted(root.glob("*/*")):
        if p.is_file() and p.suffix.lower() in MUSIC_TYPES:
            out.append({"name": p.stem, "url": file_link(p)})
            if len(out) >= limit:
                break
    return out


def music_note(folder: str) -> str:
    """Why the music folder gives no tracks, in words for the note button,
    or "" when it has some."""
    root = Path(os.path.expanduser(folder or ""))
    if not folder:
        return "No music folder is set (Settings > Home screen)"
    if not root.is_dir():
        return f"The music folder {folder} does not exist yet"
    if not music_tracks(folder, limit=1):
        return f"No music in {folder} yet (mp3, ogg, flac, m4a, mp4 ...)"
    return ""


# Words in a wallpaper's file name that say which particles suit it, checked
# before its colours. The kind with the most words in the name wins (ties go
# to the one listed first); adding a word is one entry.
AMBIENCE_WORDS: dict[str, tuple[str, ...]] = {
    "petals": (
        "sakura",
        "cherry",
        "blossom",
        "petal",
        "torii",
        "spring",
        "hanami",
        "japan",
    ),
    "snow": ("snow", "winter", "frost", "ice", "christmas", "yuki"),
    "rain": ("rain", "storm", "neon", "cyberpunk", "city", "street", "tokyo"),
    "fireflies": ("firefl", "forest", "night", "summer", "hotaru"),
    "lanterns": ("lantern", "festival", "matsuri", "shrine", "chouchin", "lamp"),
    "glitter": ("glitter", "sparkle", "magical", "idol", "crystal", "diamond"),
    "rays": ("sunbeam", "sunlight", "god-ray", "morning", "cathedral", "ray"),
    "dust": ("desert", "sand", "dune", "library", "cafe", "room", "ruin"),
    "leaves": ("autumn", "fall", "maple", "momiji", "leaves", "leaf", "harvest"),
    "bubbles": ("ocean", "sea", "underwater", "aqua", "reef", "bubble", "fish"),
    "stars": ("space", "galaxy", "star", "cosmos", "nebula", "planet", "astro"),
    "emberstorm": ("fire", "lava", "volcano", "forge", "flame", "inferno"),
    "wisps": ("spirit", "ghibli", "mononoke", "kodama", "wisp", "yokai", "ghost"),
    "galaxy": ("milkyway", "milky-way", "milky_way", "spiral", "andromeda"),
}
AMBIENCE_FILE = HOME / ".config/forge/ambience.json"  # {"file.jpg": "snow"}


def wallpaper_particle_picks() -> dict[str, str]:
    """Each wallpaper's own particle pick, set in the Wallpapers app: a kind
    or "off". A wallpaper with no pick matches its particles automatically."""
    try:
        data = json.loads(AMBIENCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    kinds = PARTICLE_LABELS.keys() - {"auto"}
    return {
        str(k): v for k, v in (data or {}).items() if isinstance(v, str) and v in kinds
    }


def set_wallpaper_particles(name: str, kind: str) -> dict[str, str]:
    """Save one wallpaper's pick; "" or "auto" clears it back to matching."""
    picks = wallpaper_particle_picks()
    if kind in ("", "auto"):
        picks.pop(name, None)
    elif kind in PARTICLE_LABELS:
        picks[name] = kind
    else:
        raise ValueError(f"no particle kind called {kind!r}")
    AMBIENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AMBIENCE_FILE.with_name(f".ambience.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(picks, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(AMBIENCE_FILE)
    return picks


def wall_light(palette: list[str]) -> float:
    """How light the picture is overall, 0..1 (the particles use it to stay
    visible on bright pictures)."""
    colours = [_hls(c) for c in palette[:6]]
    return round(sum(l for _h, l, _s in colours) / len(colours), 3) if colours else 0.0


def ambience(
    name: str, palette: list[str], overrides: dict | None = None
) -> tuple[str, str]:
    """The particle kind that suits a wallpaper, and why: the user's own pick
    for that file, a word in its name, or its colours."""
    if overrides is None:
        try:
            overrides = json.loads(AMBIENCE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            overrides = {}
    pick = (overrides or {}).get(name)
    # an "off" pick is the wallpaper's own (wallpaper_particles); what suits
    # the picture is still worked out, for when the pick is cleared
    if (
        isinstance(pick, str)
        and pick in SETTINGS["particles"]["choices"]
        and pick not in ("off", "auto")
    ):
        return pick, "your pick for this wallpaper"
    lower = name.lower()
    hits = {k: sum(w in lower for w in words) for k, words in AMBIENCE_WORDS.items()}
    best = max(hits, key=lambda k: hits[k])  # first listed wins a tie
    if hits[best]:
        return best, "its name"
    return ambience_from_colours(palette), "its colours"


def ambience_from_colours(palette: list[str]) -> str:
    """Read the most common colours the way a person would name the picture."""
    colours = [_hls(c) for c in palette[:6]]
    if not colours:
        return "soft"
    pinks = sum(
        1 for h, l, s in colours if (h > 0.86 or h < 0.02) and s > 0.25 and l > 0.5
    )
    light = sum(l for _h, l, _s in colours) / len(colours)
    sat = sum(s for _h, _l, s in colours) / len(colours)
    hue = colours[0][0]
    if pinks >= 2:
        return "petals"
    if light < 0.1 and sat < 0.4:
        return "stars"  # near black: a night sky
    if 0.45 < hue < 0.55 and sat > 0.4 and 0.25 < light < 0.6:
        return "bubbles"  # teal and cyan: water
    if 0.02 < hue < 0.1 and sat > 0.55 and 0.25 < light < 0.6:
        return "leaves"  # deep orange and rust: autumn
    if light > 0.72 and sat < 0.25:
        return "snow"
    if light < 0.32:
        if 0.5 < hue < 0.72:
            return "rain"
        return "fireflies"
    if 0.22 < hue < 0.45:
        return "fireflies"
    if 0.03 < hue < 0.17:
        return "rays" if light > 0.5 else "dust"
    return "soft"


def wallpaper_layers(settings: dict, image: Path | None, cache: Path | None) -> dict:
    """The 2.5D layers of the wallpaper's copy when they are made
    (shared/forge_layers.py); with 2.5D on and none made yet, their making
    starts in the background and the hub gets them on the next theme."""
    out = {
        "wallpaper_fg": "",
        "wallpaper_bg": "",
        "wallpaper_subject": "",
        "wallpaper_depth": "",
    }
    if not settings.get("wall_3d") or not image:
        return out
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:  # loaded by file path (forge.py) or as a module
        sys.path.insert(0, here)
    import forge_layers

    folder = Path(image).parent
    key = Path(image).stem
    info = forge_layers.ready(key, folder)
    if info is None:
        forge_layers.start(Path(image), key, folder)
        return out
    p = forge_layers.layer_paths(key, folder)
    out["wallpaper_subject"] = info.get("subject", "none")
    if out["wallpaper_subject"] != "none":
        out["wallpaper_fg"] = file_link(p["fg"])
        out["wallpaper_bg"] = file_link(p["bg"])
    # real depth works on a landscape with no subject as well
    if p["depth"].is_file():
        out["wallpaper_depth"] = file_link(p["depth"])
    return out


def theme(settings: dict | None = None, cache: Path | None = None) -> dict:
    """Everything the hub and the apps need to draw the chosen look."""
    settings = settings or load()
    source = chosen_wallpaper(settings)
    rendered = (
        render(source, cache)
        if source
        else {"image": None, "blur": None, "palette": []}
    )
    palette = rendered.get("palette") or []
    accent = settings["accent"] or (
        pick_accent(palette) if palette else FALLBACK_ACCENT
    )
    accent_2, on_accent = _variants(accent)
    layers = wallpaper_layers(settings, rendered.get("image"), cache)
    return {
        **layers,
        "wallpaper": file_link(rendered["image"]) if rendered.get("image") else "",
        "wallpaper_blur": file_link(rendered["blur"]) if rendered.get("blur") else "",
        "wallpaper_name": source.name if source else "",
        "follows_desktop": not settings["wallpaper"],
        "palette": palette,
        "swatches": swatches(palette),
        "accent": accent,
        "accent_2": accent_2,
        "on_accent": on_accent,
        "unify_accent": settings["unify_accent"],
        # Every ordinary setting as it is, so a new one reaches the hub and
        # the apps without touching this function.
        **{k: settings[k] for k, spec in SETTINGS.items() if not spec.get("custom")},
        "font_stack": FONTS.get(settings["font"], ""),
        "size_presets": size_presets(settings),
        "hub_keys": settings["hub_keys"],
        "hidden_apps": settings["hidden_apps"],
        "archived_apps": settings["archived_apps"],
        "app_folders": settings["app_folders"],
        "power_pairs": {k: list(v) for k, v in POWER_PAIRS.items()},
        "visuals_popup": popup_rows(VISUALS_POPUP),
        "music_tracks": (
            music_tracks(settings["music_folder"]) if settings["music"] else []
        ),
        "music_note": music_note(settings["music_folder"]) if settings["music"] else "",
        **dict(
            zip(
                ("ambience", "ambience_reason"),
                ambience(source.name if source else "", palette),
            )
        ),
        "wallpaper_particles": (
            wallpaper_particle_picks().get(source.name, "") if source else ""
        ),
        "wall_light": wall_light(palette),
        "surfaces": surfaces(
            accent, settings["tint"], settings["theme_source"], settings["contrast"]
        ),
    }


def write_theme_js(hub_dir: Path, data: dict) -> Path:
    # The portable launcher (forge_portable.py) serves the theme itself and
    # names a folder of its own here, so nothing is written into a release
    # folder, which may be read-only.
    hub_dir = Path(os.environ.get("FORGE_HUB_OUT") or hub_dir)
    hub_dir.mkdir(parents=True, exist_ok=True)
    out = hub_dir / "theme.js"
    tmp = out.with_name(f".theme.js.{os.getpid()}.tmp")
    tmp.write_text("window.FORGE_THEME = " + json.dumps(data) + ";\n", encoding="utf-8")
    tmp.replace(out)
    return out


def apply(hub_dir: Path) -> dict:
    """Work out the theme from the saved settings and hand it to the hub."""
    data = theme()
    write_theme_js(hub_dir, data)
    return data


# ----------------------------------------------------------------------------
# a small command line, for helpers outside Forge's apps (the GNOME desktop
# particles extension's own settings page, S21): read a group's settings with
# their current values, or change one and refresh hub/theme.js


def settings_rows(groups: list[str], settings: dict | None = None) -> list[dict]:
    """The settings in `groups`, in table order, each with its value now.
    Only the kinds a plain settings page can show: switch, number, choice."""
    settings = settings or load()
    rows = []
    for key, spec in SETTINGS.items():
        if spec.get("group") not in groups or spec.get("custom"):
            continue
        if spec["kind"] not in ("bool", "number", "int", "choice"):
            continue
        row = {k: v for k, v in spec.items() if k != "also"}
        rows.append({"key": key, **row, "value": settings.get(key, spec["default"])})
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read or change Forge settings.")
    parser.add_argument("--rows", action="append", default=[], metavar="GROUP")
    parser.add_argument("--set", metavar="KEY=JSON")
    args = parser.parse_args(argv)
    if args.set:
        key, _, raw = args.set.partition("=")
        if key not in SETTINGS:
            parser.error(f"no setting called {key!r}")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        saved = save({key: value})
        apply(Path(__file__).resolve().parent.parent / "hub")
        print(json.dumps({key: saved[key]}))
    if args.rows:
        print(json.dumps(settings_rows(args.rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
