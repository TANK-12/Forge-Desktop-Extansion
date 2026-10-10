# Forge Desktop

**Your wallpaper, alive.** Forge Desktop draws particles, a music visualiser
and widgets (clock, weather, system stats, a music player, notes and more)
right on the desktop, behind every window.

It runs on Windows, macOS and Linux. The downloads bring their own Python,
so there is nothing to install and no admin rights needed. It is the
desktop part of [Forge](https://github.com/TANK-12/Forge), split out so it
runs on its own.

## Get it

Grab the download for your system from the [Releases](../../releases) page.

| System | Start it with | How far it is tested |
| --- | --- | --- |
| Linux (GNOME 48 to 51, or Hyprland) | `sh install.sh` | Tested |
| Windows 10 / 11 | `Install.cmd` | Run under Wine only; not yet on a real PC |
| macOS (Apple silicon and Intel) | `Forge Desktop.app` | Built only; not yet run on a real Mac |

The builds are unsigned, so the first start may get a warning: on Windows
choose *More info* then *Run anyway*; on a Mac right-click the app and
choose *Open*. Bug reports from real Windows and Mac machines are very
welcome.

## How it works

The whole visual side is one web page (`desktop/particles.html`). Each
system shows it with the browser engine it already has, and only the window
holding it changes:

- **Linux:** a GTK + WebKit window per screen, kept under everything by a
  GNOME extension, or as a background layer on Hyprland.
- **Windows:** Edge WebView2 through pywebview. The scenery goes behind the
  desktop icons (the same trick animated-wallpaper apps use) and the widgets
  come up with Win+D.
- **macOS:** WKWebView through pywebview, a see-through window per screen
  just above the desktop icons, on every Space. Clicks pass through except
  on a widget.

What each system can do is listed in `desktop/platforms.json`. Where a
system cannot do something, that feature is hidden and the settings window
says why.

## Run from source

```
# Linux
sh install.sh

# Windows or macOS (Python 3.12+, with pywebview and psutil installed)
python desktop/start.py              # run it
python desktop/start.py --settings   # open the settings
python desktop/start.py --selftest   # what works on this machine, as JSON
python desktop/start.py --preview    # show the page in a normal window
```

## Privacy

The weather widget asks [Open-Meteo](https://open-meteo.com) (free, no
account) for the forecast of the town it takes from your time zone or the
one you type in. Nothing else leaves your computer. Settings stay on your
machine and the page is only served to your own computer.

## Layout

- `desktop/`: the page, the settings window, the per-system hosts
  (`host/`) and the GNOME extension
- `shared/`: widgets, particles and theme code shared with Forge
- `install.sh` / `uninstall.sh`: Linux install into your home folder, no sudo

---

Made by Emerson, with a lot of help from Claude.
