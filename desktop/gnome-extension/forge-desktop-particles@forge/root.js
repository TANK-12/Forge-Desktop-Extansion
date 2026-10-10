// Where Forge Desktop's files are (Relay task 130, 2026-10-09). A release
// install (install.sh) writes root.txt beside this extension; with none they
// are in ~/Forge, as on the owner's own machine.
import GLib from 'gi://GLib';

export function forgeRoot() {
    try {
        const [here] = GLib.filename_from_uri(import.meta.url);
        const [, bytes] = GLib.file_get_contents(
            GLib.build_filenamev([GLib.path_get_dirname(here), 'root.txt']));
        const root = new TextDecoder().decode(bytes).trim();
        if (root && GLib.path_is_absolute(root))
            return root;
    } catch (e) {
        // no root.txt, or not readable: the default below
    }
    return GLib.build_filenamev([GLib.get_home_dir(), 'Forge']);
}
