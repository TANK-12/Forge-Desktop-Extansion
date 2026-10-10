Forge Desktop 0.1.0 for Linux with GNOME 48 to 51 or Hyprland (any CPU; uses the system's Python 3)
=================================================================

Particles, widgets (clock, system, music player, notes and more) and a
music visualiser on your desktop, behind your windows. It runs on its
own; nothing else from Forge is needed.

Install
  Unpack the archive and run, from the unpacked folder:
      sh install.sh
  It copies Forge Desktop to ~/.local/share/forge-desktop (nothing
  system-wide, no sudo) and then:
  - on GNOME 48 to 51: installs and turns on its GNOME extension. Log out
    and back in once; the top bar then has its menu.
  - on Hyprland: prints the one line to add to your hyprland.conf.
  If a package it needs is missing it stops and prints the command that
  installs it on your distribution (Arch, Debian, Ubuntu, Fedora,
  openSUSE).

Use
  - The gear on the desktop, or the top bar menu on GNOME, opens the
    settings.
  - Settings are kept in ~/.config/forge/settings.json, the file Forge
    uses too. If you also run Forge, each sees a change made in the other
    after its own next settings change (or a new install).

Remove
      sh ~/.local/share/forge-desktop/uninstall.sh

What it uses on Linux
  - CPU, memory, network and drives: read from /proc and /sys
  - Music players: MPRIS over D-Bus (every Linux player)
  - The music visualiser: cava
  - Alarm and timer notifications: notify-send

Other desktops (KDE, Xfce, Sway and the rest) are not supported yet: an
ordinary window there would float over your windows instead of sitting
behind them, so it does not start.

Privacy
  The weather widget asks Open-Meteo (open-meteo.com: free, no account)
  for the forecast of the town it takes from your time zone, or the one
  you type in. Nothing else leaves this computer: the other widgets read
  this computer only.
