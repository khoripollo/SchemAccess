"""Application entry point for the SchemAccess desktop GUI.

Run with::

    python -m schemaccess.gui.app
"""

from __future__ import annotations

import sys


def main() -> int:
    """Create the QApplication and main window, then run the event loop."""
    from PySide6.QtWidgets import QApplication

    from schemaccess.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("SchemAccess")
    app.setApplicationName("SchemAccess")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
