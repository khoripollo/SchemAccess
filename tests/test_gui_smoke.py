"""GUI smoke tests (offscreen).

Skipped entirely when PySide6 is not installed.  Runs with the Qt
``offscreen`` platform plugin so no display is required: the main
window constructs, carries a window title, and the Generate button
exists and starts disabled (no input file chosen yet).
"""

from __future__ import annotations

import os

import pytest

# Must be set before the QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is not installed")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from schemaccess.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """One offscreen QApplication for the whole module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["schemaccess-tests"])
    return app


@pytest.fixture()
def window(qapp: QApplication):
    win = MainWindow()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_gui_main_window_constructs(window) -> None:
    assert window is not None
    assert window.centralWidget() is not None


def test_gui_window_title_is_set(window) -> None:
    title = window.windowTitle()
    assert title
    assert "SchemAccess" in title


def test_gui_generate_button_exists_and_starts_disabled(window) -> None:
    button = window.generate_button
    assert isinstance(button, QPushButton)
    # No input file has been chosen yet, so Generate must be disabled.
    assert window.input_edit.text() == ""
    assert not button.isEnabled()
