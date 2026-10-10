// The extension's own settings page (the gear in GNOME's Extensions app, or
// Settings… in its top bar menu).
// the owner (2026-09-27): the extension should have settings for itself, and
// for his widgets. The rows are not written out here: this page asks
// ~/Forge/shared/forge_theme.py for the "Desktop particles" and "Widgets"
// groups of Forge's settings table and builds a row for each, so a setting
// added to that table shows up here by itself. A change is saved through the
// same script, which also rewrites hub/theme.js; the desktop particles watch
// that file, so they change at once.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {forgeRoot} from './root.js';

const FORGE = forgeRoot();
const THEME = GLib.build_filenamev([FORGE, 'shared', 'forge_theme.py']);
const GROUPS = [
    ['Home screen', 'Particles on the desktop', ['desktop_particles']],
    ['On the desktop', 'On the desktop: particles, widgets, visualiser', null],
    ['Music and visualiser', 'The visualiser’s look', ['visualiser_gain', 'visualiser_opacity', 'visualiser_colours']],
    ['Widgets', 'Widgets', null],
];
const DESKTOP = GLib.build_filenamev([FORGE, 'desktop', 'particles.py']);

function run(args) {
    const proc = Gio.Subprocess.new(['python3', THEME, ...args],
        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    const [, out, err] = proc.communicate_utf8(null, null);
    if (!proc.get_successful())
        throw new Error(err || 'forge_theme.py failed');
    return out;
}

function save(key, value, row) {
    try {
        run(['--set', `${key}=${JSON.stringify(value)}`]);
    } catch (e) {
        row.subtitle = `Not saved: ${e.message}`;
    }
}

function rowFor(spec) {
    const title = spec.label || spec.key;
    const subtitle = spec.help || '';
    if (spec.kind === 'bool') {
        const row = new Adw.SwitchRow({title, subtitle, active: spec.value === true});
        row.connect('notify::active', () => save(spec.key, row.active, row));
        return row;
    }
    if (spec.kind === 'number' || spec.kind === 'int') {
        const step = spec.step || (spec.kind === 'int' ? 1 : 0.1);
        const digits = spec.kind === 'int' ? 0 : Math.max(0, -Math.floor(Math.log10(step)));
        const row = Adw.SpinRow.new_with_range(spec.min ?? 0, spec.max ?? 100, step);
        row.set_title(spec.unit ? `${title} (${spec.unit})` : title);
        row.set_subtitle(subtitle);
        row.set_digits(digits);
        row.set_value(Number(spec.value));
        row.connect('notify::value', () => save(spec.key, spec.kind === 'int' ? Math.round(row.value) : row.value, row));
        return row;
    }
    const keys = spec.choices;
    const model = Gtk.StringList.new(keys.map(k => (spec.labels || {})[k] || k));
    const row = new Adw.ComboRow({title, subtitle, model});
    row.set_selected(Math.max(0, keys.indexOf(spec.value)));
    row.connect('notify::selected', () => save(spec.key, keys[row.selected], row));
    return row;
}

export default class ForgeDesktopParticlesPrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.set_default_size(640, 760);
        const page = new Adw.PreferencesPage();
        let rows = [];
        try {
            rows = JSON.parse(run(GROUPS.flatMap(([group]) => ['--rows', group])));
        } catch (e) {
            const group = new Adw.PreferencesGroup({title: 'Forge settings could not be read', description: String(e.message)});
            page.add(group);
        }
        for (const [name, title, only] of GROUPS) {
            const mine = rows.filter(r => r.group === name && (!only || only.includes(r.key)));
            if (!mine.length)
                continue;
            const group = new Adw.PreferencesGroup({title});
            mine.forEach(spec => group.add(rowFor(spec)));
            page.add(group);
        }
        const more = new Adw.PreferencesGroup({title: 'More'});
        const editRow = new Adw.ActionRow({title: 'Edit the desktop’s widgets', subtitle: 'Add, move and remove them; Esc or Done when finished. Widgets on the desktop must be on.'});
        const editBtn = new Gtk.Button({label: 'Edit', valign: Gtk.Align.CENTER});
        editBtn.connect('clicked', () => {
            try {
                GLib.spawn_async(null, ['python3', DESKTOP, '--edit'], null, GLib.SpawnFlags.SEARCH_PATH, null);
            } catch (e) {
                editRow.subtitle = `Could not ask it: ${e.message}`;
            }
        });
        editRow.add_suffix(editBtn);
        editRow.activatable_widget = editBtn;
        more.add(editRow);
        const row = new Adw.ActionRow({title: 'All of Forge’s settings', subtitle: 'Wallpaper, particles on the home screen, Alt+V effects and the rest'});
        const open = new Gtk.Button({label: 'Open in Forge', valign: Gtk.Align.CENTER});
        open.connect('clicked', () => {
            try {
                GLib.spawn_async(null, ['python3', GLib.build_filenamev([FORGE, 'forge.py']), '--app', 'settings'], null,
                    GLib.SpawnFlags.SEARCH_PATH, null);
                window.close();
            } catch (e) {
                row.subtitle = `Could not start Forge: ${e.message}`;
            }
        });
        row.add_suffix(open);
        row.activatable_widget = open;
        // a Forge Desktop release has no Forge to open
        if (GLib.file_test(GLib.build_filenamev([FORGE, 'forge.py']), GLib.FileTest.EXISTS))
            more.add(row);
        page.add(more);
        window.add(page);
    }
}
