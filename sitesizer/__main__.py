"""``python -m sitesizer`` launches the GUI; ``python -m sitesizer.cli`` is the console flow."""

import sys


def main() -> int:
    from sitesizer.gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
