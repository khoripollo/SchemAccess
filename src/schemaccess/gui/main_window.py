"""Main window for the SchemAccess desktop GUI.

Accessibility is the product: every interactive widget carries an
accessible name and description, labels are buddy-paired with their
fields, every button and label has a keyboard mnemonic, the tab order
is explicit, and all state information is conveyed through text (never
color alone).  Progress messages are mirrored to the status bar so
screen readers announce them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from schemaccess.pipeline import PipelineOptions, PipelineResult, run_pipeline

#: GUI combo-box labels mapped to the pipeline's lowercase option values.
FORMAT_LABELS = ("PDF", "SVG", "PNG", "All")
FORMAT_VALUES = {"PDF": "pdf", "SVG": "svg", "PNG": "png", "All": "all"}

DETAIL_LABELS = ("Short", "Standard", "Detailed")
DETAIL_VALUES = {"Short": "short", "Standard": "standard",
                 "Detailed": "detailed"}

_SETTINGS_ORG = "SchemAccess"
_SETTINGS_APP = "SchemAccess"


def default_output_dir(input_path: str) -> str:
    """Return the default output folder for *input_path*.

    Per the product spec this is an ``accessible`` sub-folder next to
    the chosen schematic file.
    """
    return os.path.join(os.path.dirname(os.path.abspath(input_path)),
                        "accessible")


def make_pipeline_options(
    input_path: str,
    output_dir: str,
    generate_alt_text: bool,
    generate_image: bool,
    export_format_label: str,
    detail_label: str,
) -> PipelineOptions:
    """Translate GUI widget state into a :class:`PipelineOptions`.

    ``export_format_label`` and ``detail_label`` are the human-facing
    combo-box texts (for example ``"PDF"`` or ``"Standard"``); they are
    mapped to the lowercase values the pipeline expects.  Unknown labels
    fall back to the pipeline defaults (``"all"`` / ``"standard"``)
    rather than raising.
    """
    return PipelineOptions(
        input_path=input_path,
        output_dir=output_dir,
        generate_alt_text=generate_alt_text,
        generate_image=generate_image,
        export_format=FORMAT_VALUES.get(export_format_label, "all"),
        detail_level=DETAIL_VALUES.get(detail_label, "standard"),
    )


class PipelineWorker(QObject):
    """Runs :func:`schemaccess.pipeline.run_pipeline` off the GUI thread.

    Signals:
        progress(str):   one line per pipeline stage.
        finished(object): the :class:`PipelineResult` (even when it
                          contains errors - check ``result.errors``).
        failed(str):     an unexpected exception escaped the pipeline.
    """

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, options: PipelineOptions,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._options = options

    @Slot()
    def run(self) -> None:
        """Execute the pipeline and emit exactly one terminal signal."""
        try:
            result = run_pipeline(self._options, progress=self.progress.emit)
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.finished.emit(result)


class MainWindow(QMainWindow):
    """SchemAccess main window: pick a schematic, choose outputs, generate."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SchemAccess - Accessible KiCad Schematics")
        self.setMinimumSize(780, 640)
        self.setAcceptDrops(True)

        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._last_input_dir = ""
        self._output_dir_is_custom = False
        self._thread: Optional[QThread] = None
        self._worker: Optional[PipelineWorker] = None

        self._build_ui()
        self._restore_settings()
        self._update_dependent_widgets()
        self._update_generate_enabled()
        self.statusBar().showMessage(
            "Ready. Choose a KiCad schematic file to begin.")

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        """Create all widgets, wiring, accessibility metadata and tab order."""
        central = QWidget(self)
        outer = QVBoxLayout(central)

        # -- controls container (disabled as a block while running) -------
        self._controls = QWidget(central)
        controls_layout = QVBoxLayout(self._controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        # -- INPUT ---------------------------------------------------------
        input_group = QGroupBox("Input", self._controls)
        input_layout = QHBoxLayout(input_group)

        self.input_label = QLabel("&Input file:", input_group)
        self.input_edit = QLineEdit(input_group)
        self.input_edit.setReadOnly(True)
        self.input_edit.setPlaceholderText(
            "No file selected. Use Browse or drop a .kicad_sch file here.")
        self.input_edit.setAccessibleName("Input schematic file")
        self.input_edit.setAccessibleDescription(
            "Read-only path of the chosen KiCad schematic. Use the Browse "
            "button, or drag and drop a .kicad_sch file onto the window.")
        self.input_label.setBuddy(self.input_edit)

        self.browse_button = QPushButton("&Browse...", input_group)
        self.browse_button.setAccessibleName("Browse for schematic file")
        self.browse_button.setAccessibleDescription(
            "Open a file dialog to choose a KiCad .kicad_sch schematic file.")
        self.browse_button.clicked.connect(self._browse_input)

        input_layout.addWidget(self.input_label)
        input_layout.addWidget(self.input_edit, 1)
        input_layout.addWidget(self.browse_button)
        controls_layout.addWidget(input_group)

        # -- OUTPUT OPTIONS --------------------------------------------------
        options_group = QGroupBox("Output Options", self._controls)
        options_layout = QGridLayout(options_group)

        self.alt_text_check = QCheckBox("Generate &Alt Text", options_group)
        self.alt_text_check.setChecked(True)
        self.alt_text_check.setAccessibleName("Generate alt text")
        self.alt_text_check.setAccessibleDescription(
            "When checked, a natural-language description of the circuit "
            "is written to a text file and shown in the results area.")
        self.alt_text_check.toggled.connect(self._on_option_toggled)

        self.image_check = QCheckBox("Generate I&mage", options_group)
        self.image_check.setChecked(True)
        self.image_check.setAccessibleName("Generate image")
        self.image_check.setAccessibleDescription(
            "When checked, a CircuiTikZ rendering of the schematic is "
            "exported in the chosen format.")
        self.image_check.toggled.connect(self._on_option_toggled)

        self.format_label = QLabel("&Export format:", options_group)
        self.format_combo = QComboBox(options_group)
        self.format_combo.addItems(list(FORMAT_LABELS))
        self.format_combo.setCurrentText("All")
        self.format_combo.setAccessibleName("Export format")
        self.format_combo.setAccessibleDescription(
            "Image export format: PDF, SVG, PNG, or All three. Only used "
            "when Generate Image is checked.")
        self.format_label.setBuddy(self.format_combo)

        self.detail_label = QLabel("&Description detail:", options_group)
        self.detail_combo = QComboBox(options_group)
        self.detail_combo.addItems(list(DETAIL_LABELS))
        self.detail_combo.setCurrentText("Standard")
        self.detail_combo.setAccessibleName("Description detail level")
        self.detail_combo.setAccessibleDescription(
            "How verbose the alt text description is: Short, Standard, or "
            "Detailed. Only used when Generate Alt Text is checked.")
        self.detail_label.setBuddy(self.detail_combo)

        options_layout.addWidget(self.alt_text_check, 0, 0)
        options_layout.addWidget(self.image_check, 0, 1)
        options_layout.addWidget(self.detail_label, 1, 0)
        options_layout.addWidget(self.detail_combo, 1, 1)
        options_layout.addWidget(self.format_label, 2, 0)
        options_layout.addWidget(self.format_combo, 2, 1)
        controls_layout.addWidget(options_group)

        # -- OUTPUT FOLDER ---------------------------------------------------
        folder_group = QGroupBox("Output Folder", self._controls)
        folder_layout = QHBoxLayout(folder_group)

        self.output_label = QLabel("Output &folder:", folder_group)
        self.output_edit = QLineEdit(folder_group)
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText(
            "Defaults to an 'accessible' folder next to the input file.")
        self.output_edit.setAccessibleName("Output folder")
        self.output_edit.setAccessibleDescription(
            "Read-only path of the folder where generated files are "
            "written. Use the Choose button to change it.")
        self.output_label.setBuddy(self.output_edit)

        self.choose_button = QPushButton("C&hoose...", folder_group)
        self.choose_button.setAccessibleName("Choose output folder")
        self.choose_button.setAccessibleDescription(
            "Open a folder dialog to choose where generated files are "
            "written.")
        self.choose_button.clicked.connect(self._choose_output_dir)

        folder_layout.addWidget(self.output_label)
        folder_layout.addWidget(self.output_edit, 1)
        folder_layout.addWidget(self.choose_button)
        controls_layout.addWidget(folder_group)

        # -- GENERATE --------------------------------------------------------
        self.generate_button = QPushButton("&Generate", self._controls)
        self.generate_button.setDefault(True)
        self.generate_button.setAutoDefault(True)
        self.generate_button.setAccessibleName("Generate outputs")
        self.generate_button.setAccessibleDescription(
            "Run the conversion: read the schematic and produce the "
            "selected alt text and image outputs. Enabled once an input "
            "file is selected and at least one output option is checked.")
        self.generate_button.clicked.connect(self._on_generate)
        controls_layout.addWidget(self.generate_button)

        outer.addWidget(self._controls)

        # -- PROGRESS --------------------------------------------------------
        progress_group = QGroupBox("Progress", central)
        progress_layout = QVBoxLayout(progress_group)
        self.progress_label = QLabel("&Progress log:", progress_group)
        self.progress_log = QPlainTextEdit(progress_group)
        self.progress_log.setReadOnly(True)
        self.progress_log.setAccessibleName("Progress log")
        self.progress_log.setAccessibleDescription(
            "Read-only log of pipeline stages, warnings, errors and the "
            "paths of produced files.")
        self.progress_label.setBuddy(self.progress_log)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_log)
        outer.addWidget(progress_group, 1)

        # -- RESULTS ---------------------------------------------------------
        results_group = QGroupBox("Results", central)
        results_layout = QVBoxLayout(results_group)

        self.results_label = QLabel("Al&t text result:", results_group)
        self.results_edit = QPlainTextEdit(results_group)
        self.results_edit.setReadOnly(True)
        self.results_edit.setAccessibleName("Generated alt text")
        self.results_edit.setAccessibleDescription(
            "Read-only text area containing the generated natural-language "
            "description of the schematic, readable in-app by screen "
            "readers.")
        self.results_label.setBuddy(self.results_edit)

        self.preview_label = QLabel(results_group)
        self.preview_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.preview_label.setAccessibleName("Schematic image preview")
        self.preview_label.setVisible(False)

        self.open_folder_button = QPushButton("Open &Output Folder",
                                              results_group)
        self.open_folder_button.setAccessibleName("Open output folder")
        self.open_folder_button.setAccessibleDescription(
            "Open the output folder in the system file manager.")
        self.open_folder_button.clicked.connect(self._open_output_folder)

        results_layout.addWidget(self.results_label)
        results_layout.addWidget(self.results_edit, 1)
        results_layout.addWidget(self.preview_label)
        results_layout.addWidget(self.open_folder_button)
        outer.addWidget(results_group, 1)

        self.setCentralWidget(central)

        # -- logical tab order -----------------------------------------------
        QWidget.setTabOrder(self.input_edit, self.browse_button)
        QWidget.setTabOrder(self.browse_button, self.alt_text_check)
        QWidget.setTabOrder(self.alt_text_check, self.image_check)
        QWidget.setTabOrder(self.image_check, self.detail_combo)
        QWidget.setTabOrder(self.detail_combo, self.format_combo)
        QWidget.setTabOrder(self.format_combo, self.output_edit)
        QWidget.setTabOrder(self.output_edit, self.choose_button)
        QWidget.setTabOrder(self.choose_button, self.generate_button)
        QWidget.setTabOrder(self.generate_button, self.progress_log)
        QWidget.setTabOrder(self.progress_log, self.results_edit)
        QWidget.setTabOrder(self.results_edit, self.open_folder_button)

    # ------------------------------------------------------------ settings

    def _restore_settings(self) -> None:
        """Load persisted UI state from QSettings (graceful on bad values)."""
        s = self._settings
        self._last_input_dir = str(s.value("input/last_dir", "", type=str))
        output_dir = str(s.value("output/dir", "", type=str))
        if output_dir:
            self.output_edit.setText(output_dir)
        self.alt_text_check.setChecked(
            bool(s.value("options/generate_alt_text", True, type=bool)))
        self.image_check.setChecked(
            bool(s.value("options/generate_image", True, type=bool)))
        fmt = str(s.value("options/export_format", "All", type=str))
        if fmt in FORMAT_LABELS:
            self.format_combo.setCurrentText(fmt)
        detail = str(s.value("options/detail_level", "Standard", type=str))
        if detail in DETAIL_LABELS:
            self.detail_combo.setCurrentText(detail)

    def _save_settings(self) -> None:
        """Persist UI state to QSettings."""
        s = self._settings
        s.setValue("input/last_dir", self._last_input_dir)
        s.setValue("output/dir", self.output_edit.text())
        s.setValue("options/generate_alt_text",
                   self.alt_text_check.isChecked())
        s.setValue("options/generate_image", self.image_check.isChecked())
        s.setValue("options/export_format", self.format_combo.currentText())
        s.setValue("options/detail_level", self.detail_combo.currentText())
        s.sync()

    # --------------------------------------------------------- interactions

    def _browse_input(self) -> None:
        """Show a file dialog filtered to KiCad schematic files."""
        start_dir = self._last_input_dir or os.path.expanduser("~")
        path, _selected = QFileDialog.getOpenFileName(
            self, "Choose a KiCad schematic", start_dir,
            "KiCad schematics (*.kicad_sch);;All files (*)")
        if path:
            self._set_input_file(path)

    def _choose_output_dir(self) -> None:
        """Show a directory dialog for the output folder."""
        start_dir = self.output_edit.text() or self._last_input_dir or \
            os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, "Choose the output folder", start_dir)
        if path:
            self.output_edit.setText(os.path.normpath(path))
            self._output_dir_is_custom = True

    def _set_input_file(self, path: str) -> None:
        """Record *path* as the chosen schematic and derive defaults."""
        path = os.path.normpath(os.path.abspath(path))
        self.input_edit.setText(path)
        self._last_input_dir = os.path.dirname(path)
        if not self._output_dir_is_custom:
            self.output_edit.setText(os.path.normpath(
                default_output_dir(path)))
        self._update_generate_enabled()
        self.statusBar().showMessage(
            f"Selected {os.path.basename(path)}. Press Generate when ready.")

    def _on_option_toggled(self, _checked: bool) -> None:
        self._update_dependent_widgets()
        self._update_generate_enabled()

    def _update_dependent_widgets(self) -> None:
        """Enable/disable the combos that depend on the checkboxes."""
        image_on = self.image_check.isChecked()
        self.format_combo.setEnabled(image_on)
        self.format_label.setEnabled(image_on)
        alt_on = self.alt_text_check.isChecked()
        self.detail_combo.setEnabled(alt_on)
        self.detail_label.setEnabled(alt_on)

    def _update_generate_enabled(self) -> None:
        """Generate needs an input file and at least one output selected."""
        has_input = bool(self.input_edit.text())
        any_output = (self.alt_text_check.isChecked()
                      or self.image_check.isChecked())
        self.generate_button.setEnabled(has_input and any_output)

    def current_options(self) -> PipelineOptions:
        """Return :class:`PipelineOptions` reflecting the current UI state."""
        return make_pipeline_options(
            input_path=self.input_edit.text(),
            output_dir=self.output_edit.text(),
            generate_alt_text=self.alt_text_check.isChecked(),
            generate_image=self.image_check.isChecked(),
            export_format_label=self.format_combo.currentText(),
            detail_label=self.detail_combo.currentText(),
        )

    # ------------------------------------------------------------- pipeline

    def _on_generate(self) -> None:
        """Start the pipeline in a worker thread; keep the UI responsive."""
        if self._thread is not None:
            return  # already running
        options = self.current_options()
        if not options.output_dir:
            options.output_dir = default_output_dir(options.input_path)
            self.output_edit.setText(os.path.normpath(options.output_dir))

        self.progress_log.clear()
        self.results_edit.clear()
        self.preview_label.clear()
        self.preview_label.setVisible(False)
        self._set_running(True)

        self._worker = PipelineWorker(options)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._append_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._release_worker)
        self._thread.start()

    def _set_running(self, running: bool) -> None:
        """Disable (not freeze) the controls while the pipeline runs."""
        self._controls.setEnabled(not running)
        self.open_folder_button.setEnabled(not running)
        if running:
            self.statusBar().showMessage("Generating...")

    @Slot(str)
    def _append_progress(self, line: str) -> None:
        """Append one line to the progress log and mirror it on the status
        bar."""
        self.progress_log.appendPlainText(line)
        self.statusBar().showMessage(line)

    @Slot(object)
    def _on_finished(self, result_obj: object) -> None:
        """Show warnings, errors, produced files, alt text and PNG preview."""
        result = result_obj if isinstance(result_obj, PipelineResult) else None
        self._set_running(False)
        if result is None:
            self._report_failure("Pipeline returned an unexpected result.")
            return

        for warning in result.warnings:
            self._append_progress(f"Warning: {warning}")
        for error in result.errors:
            self._append_progress(f"Error: {error}")
        for kind in sorted(result.output_files):
            self._append_progress(
                f"Produced {kind}: {result.output_files[kind]}")

        if result.alt_text:
            self.results_edit.setPlainText(result.alt_text)

        png_path = result.output_files.get("png", "")
        if png_path and os.path.isfile(png_path):
            self._show_preview(png_path, result.alt_text)

        if result.ok:
            self._append_progress("Finished successfully.")
            self.statusBar().showMessage("Finished successfully.")
        else:
            summary = "\n".join(result.errors)
            self.statusBar().showMessage("Finished with errors.")
            QMessageBox.critical(self, "SchemAccess - Conversion failed",
                                 f"The conversion reported errors:\n\n"
                                 f"{summary}")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        """An unexpected exception escaped the pipeline."""
        self._set_running(False)
        self._report_failure(message)

    def _report_failure(self, message: str) -> None:
        self._append_progress(f"Error: {message}")
        self.statusBar().showMessage("Failed.")
        QMessageBox.critical(self, "SchemAccess - Error", message)

    @Slot()
    def _release_worker(self) -> None:
        """Drop worker/thread references once the thread has stopped."""
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def _show_preview(self, png_path: str, alt_text: str) -> None:
        """Display a scaled preview of the rendered PNG with alt text."""
        pixmap = QPixmap(png_path)
        if pixmap.isNull():
            self._append_progress(
                f"Warning: could not load preview image {png_path}")
            return
        scaled = pixmap.scaled(
            560, 300,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.preview_label.setPixmap(scaled)
        description = alt_text or f"Rendered schematic image {png_path}"
        self.preview_label.setAccessibleDescription(description)
        self.preview_label.setToolTip(description)
        self.preview_label.setVisible(True)

    def _open_output_folder(self) -> None:
        """Open the output folder in the platform file manager."""
        folder = self.output_edit.text()
        if not folder or not os.path.isdir(folder):
            self._append_progress(
                "Warning: the output folder does not exist yet. "
                "Run Generate first.")
            self.statusBar().showMessage("Output folder does not exist yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa: S606 - opening a folder
            else:
                subprocess.Popen(["xdg-open", folder])  # noqa: S603, S607
        except OSError as exc:
            self._report_failure(f"Could not open the output folder: {exc}")

    # ---------------------------------------------------------- Qt events

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        """Accept a drag that carries a local .kicad_sch file."""
        if self._schematic_from_mime(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """Use a dropped .kicad_sch file as the input schematic."""
        path = self._schematic_from_mime(event)
        if path is not None:
            self._set_input_file(path)
            event.acceptProposedAction()
        else:
            event.ignore()

    @staticmethod
    def _schematic_from_mime(event: QDragEnterEvent) -> Optional[str]:
        """Return the first dropped local .kicad_sch path, else None."""
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                local = url.toLocalFile()
                if local.lower().endswith(".kicad_sch"):
                    return local
        return None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Persist settings and wait for a running worker before closing."""
        self._save_settings()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(5000)
        super().closeEvent(event)
