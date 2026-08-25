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


def test_gui_has_no_detail_choice(window):
    """The GUI always writes the detailed description, so there is no
    detail dropdown to get wrong."""
    from schemaccess.gui.main_window import GUI_DETAIL_LEVEL

    assert not hasattr(window, "detail_combo")
    assert not hasattr(window, "detail_label")
    window.input_edit.setText("board.kicad_sch")
    assert window.current_options().detail_level == GUI_DETAIL_LEVEL
    assert GUI_DETAIL_LEVEL == "detailed"


def test_gui_shows_a_conversion_summary(window):
    """The results area reports counts in and counts converted."""
    from schemaccess.pipeline import ConversionStats

    assert window.summary_edit.isReadOnly()
    assert window.summary_edit.accessibleName() == "Conversion summary"

    stats = ConversionStats(symbols=28, components=25, nets=46, nodes=11,
                            drawn=24, described=25, fallbacks=["TR1"],
                            has_drawing=True, has_text=True)
    text = "\n".join(stats.summary_lines())
    assert "25 components in the KiCad schematic (28 symbols placed)." in text
    assert "11 nodes (46 nets in total)." in text
    assert "24 of 25 components converted to CircuiTikZ symbols." in text
    assert "25 of 25 components described in the alt text." in text
    assert "TR1" in text, "a component that did not convert must be named"


def test_conversion_summary_omits_outputs_that_were_not_produced():
    """A text-only run must not claim '0 converted' for a drawing that was
    never requested."""
    from schemaccess.pipeline import ConversionStats

    stats = ConversionStats(symbols=3, components=2, nets=2, nodes=2,
                            described=2, has_text=True)
    text = "\n".join(stats.summary_lines())
    assert "converted to CircuiTikZ" not in text
    assert "2 of 2 components described in the alt text." in text
