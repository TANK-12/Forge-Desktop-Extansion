"""Start Forge Desktop on Windows or macOS (Relay task 130, 2026-10-09).

    python desktop/start.py              run it (a second start opens the settings)
    python desktop/start.py --settings   open the settings (--edit, --quit likewise)
    python desktop/start.py --selftest   what works on this machine, as JSON; no windows
    python desktop/start.py --preview    the page in an ordinary window, on any OS

On Linux the desktop itself runs through particles.py (install.sh sets that
up); this start is for Windows, macOS and the preview. See desktop/README.md.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent / "shared"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from host.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
