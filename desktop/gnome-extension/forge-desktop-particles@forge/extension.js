// Forge on the desktop (B38 particles; S25 widgets and a visualiser). GNOME
// Wayland has no layer-shell, so a normal window can't sit under the other
// windows by itself. This extension starts ~/Forge/desktop/particles.py as a
// Wayland client it owns while any of Settings > On the desktop's switches is
// on (particles, widgets, a visualiser), then makes that one window a desktop
// window: below every other window, on every workspace, out of Alt+Tab and
// the overview, and covering its monitor: the script makes one per monitor
// (S24), the main one on the primary monitor. Clicks pass through it
// except where a widget is (the script sets the input region from the page).
// Forge itself need not be running: the script reads Forge's files only.
//
// S25 (the owner, 2026-09-28: "make the desktop extension work for more than
// just particles"): a top bar button with a switch for each of the three,
// "Edit desktop widgets" and the settings page. A switch is saved through
// ~/Forge/shared/forge_theme.py, which rewrites hub/theme.js, so the running
// script and Forge both follow it.
//
// Super's overview (the owner, 2026-09-28: the desktop "disappears when I do
// the super press"): GNOME hides every window while it shows the overview,
// this one too. Each zoomed-out desktop card (workspace.js's
// WorkspaceBackground, one per workspace and monitor) gets a live copy of
// that monitor's desktop window just above its wallpaper, so the particles,
// widgets and visualiser zoom out with the desktop. The copy is a
// Clutter.Clone: it draws the same window again, nothing is run twice.
// Settings > On the desktop > Keep them in the overview turns it off.
//
// It also owns org.forge.Desktop on the session bus with one method,
// Activate(pids, desktop): the hub's Now playing "Go to it" for players
// outside Forge. GNOME ignores a window raising itself, but the shell may
// activate any window. The first window whose process is in pids wins, else
// one whose app id or class matches the player's desktop entry.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Workspace from 'resource:///org/gnome/shell/ui/workspace.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

import {SHOWN_LEAST, clip, shownShare} from './cover.js';
import {forgeRoot} from './root.js';

const ROOT = forgeRoot();
const SCRIPT = GLib.build_filenamev([ROOT, 'desktop', 'particles.py']);
// Which screens a maximised or full-screen window hides, for particles.py
// (UNDER_FILE there), so the desktop stops drawing where nobody sees it.
const UNDER = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'forge-desktop-under.json']);
// Forge's own windows go in the same file under "pids" (shared/forge_seen.py),
// so forge.py rests too (task 60).

// forge.py and forge_popout.py windows (GLib.set_prgname "Forge...")
function isForge(win) {
    const name = win.get_gtk_application_id() || win.get_wm_class() || '';
    return name.toLowerCase().startsWith('forge');
}
const IFACE = `<node><interface name="org.forge.Desktop">
  <method name="Activate"><arg type="au" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
</interface></node>`;
// A copy that asks for no size of its own. A plain Clutter.Clone asks for its
// window's full size (the monitor), the card's width is worked out from its
// children's, so the card came out screen wide and the wallpaper and the
// copy were stretched sideways to fill it (the owner, 2026-09-29: "squished
// now and wrong aspect"). The card's own layout still stretches it to fill.
const CardClone = GObject.registerClass(
class CardClone extends Clutter.Clone {
    vfunc_get_preferred_width(_forHeight) {
        return [0, 0];
    }

    vfunc_get_preferred_height(_forWidth) {
        return [0, 0];
    }
});

// forge_theme.py's own rule (FORGE_CONFIG, else ~/.config/forge/settings.json):
// it never reads XDG_CONFIG_HOME, so a desktop that sets it must not either
const SETTINGS = GLib.getenv('FORGE_CONFIG') ||
    GLib.build_filenamev([GLib.get_home_dir(), '.config', 'forge', 'settings.json']);
const THEME = GLib.build_filenamev([ROOT, 'shared', 'forge_theme.py']);

export default class ForgeDesktopParticles extends Extension {
    enable() {
        this._client = null;
        this._proc = null;
        this._windows = new Map(); // screen number -> its desktop window (S24)
        this._settings = Gio.File.new_for_path(SETTINGS);
        this._monitor = this._settings.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, null);
        this._monitorId = this._monitor.connect('changed', () => this._later());
        this._createdId = global.display.connect('window-created', (_d, win) => this._adopt(win));
        this._screensId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/forge/Desktop');
        this._nameId = Gio.bus_own_name(Gio.BusType.SESSION, 'org.forge.Desktop', Gio.BusNameOwnerFlags.NONE, null, null, null);
        this._editSoon = 0;
        this._button = null;
        this._inOverview = true;
        this._layers = new Set(); // the overview's copies, see _addLayer
        this._injections = new InjectionManager();
        const ext = this;
        this._injections.overrideMethod(Workspace.WorkspaceBackground.prototype, '_init', original => function (...args) {
            original.call(this, ...args);
            ext._addLayer(this._backgroundGroup, this._monitorIndex);
        });
        // cards the overview made before this was enabled (at log in, or
        // after the lock screen turned the extension off and on again)
        this._findCards(Main.layoutManager.overviewGroup);
        this._sync();
        this._watchUnder();
    }

    // --- resting under a big window (the owner, 2026-09-29: the desktop must
    // stop using the GPU when it is not seen) ----------------------------------
    // GNOME says "fully covered" only when every pixel is, and the top bar or
    // a dock always leaves a strip, so a maximised window never counted. The
    // shell knows the windows: a screen whose work area is at least nine
    // tenths under windows on the current workspace is hidden (task 60: a big
    // window that is not maximised counts too). Forge's own windows get the
    // same test, plus minimised and on another workspace. Every window counts
    // as cover, see-through or not. The overview shows every desktop and
    // window again, so nothing is hidden while it is open.
    _watchUnder() {
        this._underText = '';
        this._underSoon = 0;
        const again = () => this._underLater();
        const wm = global.window_manager;
        this._underIds = [
            [global.display, global.display.connect('restacked', again)],
            [global.display, global.display.connect('in-fullscreen-changed', again)],
            [global.display, global.display.connect('grab-op-end', again)],
            [global.display, global.display.connect('window-entered-monitor', again)],
            [global.workspace_manager, global.workspace_manager.connect('active-workspace-changed', again)],
            [wm, wm.connect('size-changed', again)],
            [wm, wm.connect('minimize', again)],
            [wm, wm.connect('unminimize', again)],
            [wm, wm.connect('map', again)],
            [wm, wm.connect('destroy', again)],
            [Main.overview, Main.overview.connect('showing', again)],
            [Main.overview, Main.overview.connect('hidden', again)],
        ];
        this._underLater();
    }

    _underLater() {
        if (this._underSoon)
            return;
        this._underSoon = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
            this._underSoon = 0;
            this._writeUnder();
            return GLib.SOURCE_REMOVE;
        });
    }

    _writeUnder() {
        const out = {};
        const open = Main.overview.visible;
        const ws = global.workspace_manager.get_active_workspace();
        // the windows that show on this workspace, bottom to top
        const stack = global.display.sort_windows_by_stacking(ws.list_windows().filter(w =>
            !this._isOurs(w) && !w.minimized && w.showing_on_its_workspace() &&
            w.get_window_type() !== Meta.WindowType.DESKTOP));
        // how much shows, in twentieths, for the reduced tier
        // (shared/forge_power.js): {"screens": {...}, "pids": {...}}
        const shown = {screens: {}, pids: {}};
        const step = share => Math.round(share * 20) / 20;
        for (const screen of this._windows.keys()) {
            const monitor = this._monitorOf(screen);
            const share = open || monitor === undefined ? 1
                : shownShare(ws.get_work_area_for_monitor(monitor), stack.map(w => w.get_frame_rect()));
            out[screen] = share < SHOWN_LEAST;
            shown.screens[screen] = step(share);
        }
        // a Forge process shows as much as its main window (title "Forge",
        // forge.py); its pop-out pads share the pid but not the hub's page.
        // A process with no such window counts its most visible one.
        const pids = {}, mains = {};
        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (!win || this._isOurs(win) || !isForge(win) || win.get_pid() <= 0)
                continue;
            const pid = win.get_pid();
            const share = open ? 1 : this._windowShare(win, ws, stack);
            if (win.get_title() === 'Forge')
                mains[pid] = Math.max(mains[pid] ?? 0, share);
            pids[pid] = Math.max(pids[pid] ?? 0, share);
        }
        Object.assign(pids, mains);
        out.pids = {};
        for (const [pid, share] of Object.entries(pids)) {
            out.pids[pid] = share < SHOWN_LEAST;
            shown.pids[pid] = step(share);
        }
        out.shown = shown;
        const text = JSON.stringify(out);
        if (text === this._underText)
            return;
        this._underText = text;
        try {
            GLib.file_set_contents(UNDER, text);
        } catch (e) {
            logError(e, 'Forge desktop particles: could not write the hidden screens');
        }
    }

    // the share of a window that shows: 0 minimised or on another workspace
    _windowShare(win, ws, stack) {
        if (win.minimized || (!win.is_on_all_workspaces() && win.get_workspace() !== ws))
            return 0;
        const at = stack.indexOf(win);
        if (at < 0)
            return 0;
        const rect = clip(win.get_frame_rect(), global.display.get_monitor_geometry(win.get_monitor()));
        return shownShare(rect, stack.slice(at + 1).map(w => w.get_frame_rect()));
    }

    // --- the overview's copies ----------------------------------------------
    _findCards(actor) {
        if (actor instanceof Workspace.WorkspaceBackground) {
            this._addLayer(actor._backgroundGroup, actor._monitorIndex);
            return;
        }
        for (const child of actor.get_children())
            this._findCards(child);
    }

    // group is the card's wallpaper group, sized to the whole monitor as the
    // card zooms; the clone fills it the same way the wallpaper does
    _addLayer(group, monitor) {
        if (!group)
            return;
        const clone = new CardClone();
        group.add_child(clone);
        // a new wallpaper is added on top of the group; keep the copy over it
        group.connectObject('child-added', (_g, child) => {
            if (child !== clone)
                group.set_child_above_sibling(clone, null);
        }, clone);
        const layer = {clone, monitor};
        this._layers.add(layer);
        clone.connect('destroy', () => this._layers.delete(layer));
        this._fillLayer(layer);
    }

    _fillLayer(layer) {
        let actor = null;
        if (this._inOverview) {
            for (const [screen, win] of this._windows) {
                if (this._monitorOf(screen) === layer.monitor)
                    actor = win.get_compositor_private();
            }
        }
        layer.clone.source = actor;
    }

    _fillLayers() {
        for (const layer of this._layers)
            this._fillLayer(layer);
    }

    // --- the top bar button -------------------------------------------------
    _makeButton() {
        const button = new PanelMenu.Button(0.0, 'Forge on the desktop', false);
        button.add_child(new St.Icon({icon_name: 'preferences-desktop-wallpaper-symbolic', style_class: 'system-status-icon'}));
        const menu = button.menu;
        const particles = new PopupMenu.PopupSwitchMenuItem('Particles', false);
        const widgets = new PopupMenu.PopupSwitchMenuItem('Widgets', false);
        const visual = new PopupMenu.PopupSwitchMenuItem('Visualiser', false);
        particles.connect('toggled', (_i, on) => this._set('desktop_particles', on));
        widgets.connect('toggled', (_i, on) => this._set('desktop_widgets', on));
        // the visualiser's switch takes the home screen's style, or bars
        visual.connect('toggled', (_i, on) => {
            const s = this._read();
            const style = s.visualiser && s.visualiser !== 'off' ? s.visualiser : 'bars';
            this._set('desktop_visualiser', on ? style : 'off');
        });
        const edit = new PopupMenu.PopupMenuItem('Edit desktop widgets');
        edit.connect('activate', () => this._edit());
        const prefs = new PopupMenu.PopupMenuItem('Settings…');
        prefs.connect('activate', () => this.openPreferences());
        [particles, widgets, visual, new PopupMenu.PopupSeparatorMenuItem(), edit, prefs].forEach(i => menu.addMenuItem(i));
        // the switches show the saved settings each time the menu opens
        menu.connect('open-state-changed', (_m, open) => {
            if (!open)
                return;
            const s = this._read();
            particles.setToggleState(s.desktop_particles === true);
            widgets.setToggleState(s.desktop_widgets === true);
            visual.setToggleState(!!s.desktop_visualiser && s.desktop_visualiser !== 'off');
        });
        Main.panel.addToStatusArea(this.uuid, button);
        return button;
    }

    _showButton() {
        const want = this._read().desktop_panel_icon !== false;
        if (want && !this._button)
            this._button = this._makeButton();
        else if (!want && this._button) {
            this._button.destroy();
            this._button = null;
        }
    }

    // saved through forge_theme.py, off the shell's own thread of work
    _set(key, value) {
        try {
            const proc = Gio.Subprocess.new(['python3', THEME, '--set', `${key}=${JSON.stringify(value)}`],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
            proc.wait_async(null, null);
        } catch (e) {
            logError(e, 'Forge on the desktop: could not save a setting');
        }
    }

    // widgets on (if they were off), then the script starts editing them
    _edit() {
        if (this._read().desktop_widgets !== true)
            this._set('desktop_widgets', true);
        if (this._editSoon)
            GLib.source_remove(this._editSoon);
        let tries = 0;
        this._editSoon = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
            if (this._proc && this._windows.size) {
                // particles.py --edit tells the running one through a file it
                // watches; a signal would clash with WebKit's own (S26)
                try {
                    Gio.Subprocess.new(['/usr/bin/python3', SCRIPT, '--edit'], Gio.SubprocessFlags.STDERR_SILENCE);
                } catch (e) {
                    logError(e, 'Forge on the desktop: could not ask it to edit');
                }
                this._editSoon = 0;
                return GLib.SOURCE_REMOVE;
            }
            if (++tries > 20) {
                this._editSoon = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    Activate(pids, desktop) {
        const wins = global.get_window_actors().map(a => a.meta_window)
            .filter(w => w && !this._isOurs(w) && !w.skip_taskbar);
        let win = null;
        for (const pid of pids) {
            win = wins.find(w => w.get_pid() === pid);
            if (win)
                break;
        }
        const d = (desktop || '').toLowerCase();
        if (!win && d) {
            win = wins.find(w => [w.get_gtk_application_id(), w.get_sandboxed_app_id(), w.get_wm_class()]
                .some(id => id && id.toLowerCase().includes(d)));
        }
        if (!win)
            return false;
        Main.activateWindow(win);
        return true;
    }

    disable() {
        // no stale answer left behind: no file reads as all shown
        GLib.unlink(UNDER);
        for (const [obj, id] of this._underIds || [])
            obj.disconnect(id);
        this._underIds = [];
        if (this._underSoon)
            GLib.source_remove(this._underSoon);
        this._underSoon = 0;
        this._injections.clear();
        this._injections = null;
        for (const layer of [...this._layers])
            layer.clone.destroy();
        this._layers.clear();
        this._dbus.unexport();
        this._dbus = null;
        Gio.bus_unown_name(this._nameId);
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = 0;
        if (this._editSoon) GLib.source_remove(this._editSoon);
        this._editSoon = 0;
        if (this._button) this._button.destroy();
        this._button = null;
        this._monitor.disconnect(this._monitorId);
        this._monitor.cancel();
        global.display.disconnect(this._createdId);
        Main.layoutManager.disconnect(this._screensId);
        this._stop();
        this._monitor = null;
        this._settings = null;
    }

    // settings.json is replaced by a rename; let a burst of events settle
    _later() {
        if (this._timer) return;
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
            this._timer = 0;
            this._sync();
            return GLib.SOURCE_REMOVE;
        });
    }

    _read() {
        try {
            const [, bytes] = this._settings.load_contents(null);
            const s = JSON.parse(new TextDecoder().decode(bytes));
            return s && typeof s === 'object' ? s : {};
        } catch (e) {
            return {};
        }
    }

    // the same rule as wanted() in desktop/particles.py
    _wanted() {
        const s = this._read();
        return s.desktop_particles === true || s.desktop_widgets === true ||
            (!!s.desktop_visualiser && s.desktop_visualiser !== 'off');
    }

    _sync() {
        this._showButton();
        const inOverview = this._read().desktop_in_overview !== false;
        if (inOverview !== this._inOverview) {
            this._inOverview = inOverview;
            this._fillLayers();
        }
        if (this._wanted())
            this._start();
        else
            this._stop();
    }

    _start() {
        if (this._proc)
            return;
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        launcher.setenv('FORGE_DESKTOP_PARTICLES_HOST', 'gnome-extension', true);
        try {
            this._client = Meta.WaylandClient.new_subprocess(global.context, launcher, ['/usr/bin/python3', SCRIPT]);
        } catch (e) {
            logError(e, 'Forge desktop particles: could not start the script');
            return;
        }
        const proc = this._client.get_subprocess();
        this._proc = proc;
        // the script also quits by itself when the setting goes off
        proc.wait_async(null, () => {
            if (this._proc !== proc)
                return;
            this._proc = null;
            this._client = null;
            this._windows.clear();
            this._fillLayers();
        });
    }

    _stop() {
        if (this._proc)
            this._proc.send_signal(15);
        this._proc = null;
        this._client = null;
        this._windows.clear();
        this._fillLayers();
    }

    _isOurs(win) {
        return [...this._windows.values()].includes(win);
    }

    // S24: the script makes one window per monitor, titled
    // "forge-desktop-particles" for the main screen and "... #<n>" for the
    // others (desktop/particles.py window_title); each becomes a desktop
    // window on its own monitor.
    _adopt(win) {
        if (!this._client || !this._client.owns_window(win))
            return;
        const id = win.connect('shown', () => {
            win.disconnect(id);
            const m = /^forge-desktop-particles(?: #(\d+))?$/.exec(win.get_title() || '');
            const screen = m && m[1] ? Number(m[1]) : 0;
            this._windows.set(screen, win);
            win.connect('unmanaged', () => {
                if (this._windows.get(screen) === win)
                    this._windows.delete(screen);
                this._fillLayers();
            });
            win.set_type(Meta.WindowType.DESKTOP);
            win.stick();
            win.lower();
            win.connect('raised', () => win.lower());
            this._place();
            this._underLater();
        });
    }

    // screen 0 on the primary monitor, the others on the rest in order
    _monitorOf(screen) {
        const primary = global.display.get_primary_monitor();
        if (screen === 0)
            return primary;
        const others = [];
        for (let i = 0; i < global.display.get_n_monitors(); i++) {
            if (i !== primary)
                others.push(i);
        }
        return others[screen - 1];
    }

    _place() {
        for (const [screen, win] of this._windows) {
            const monitor = this._monitorOf(screen);
            if (monitor === undefined)
                continue;
            const area = global.display.get_monitor_geometry(monitor);
            win.move_resize_frame(false, area.x, area.y, area.width, area.height);
        }
        this._fillLayers();
    }
}
