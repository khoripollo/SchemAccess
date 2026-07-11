"""LaTeX renderer: compile generated .tex to PDF / SVG / PNG.

Drives the local LaTeX toolchain (pdflatex plus a PDF-to-vector/raster
converter) discovered on ``PATH``:

* PDF  -- ``pdflatex`` (required for everything).
* SVG  -- ``pdftocairo`` (preferred) or ``dvisvgm``.
* PNG  -- ``pdftocairo`` (preferred) or ``pdftoppm``.

Discovery happens once per :class:`Renderer` instance and the resolved
executable paths are exposed through the :attr:`Renderer.tools` dict for
diagnostics.  All external tools are invoked with explicit argument lists
(never ``shell=True``) and a hard timeout.  Freshly compiled PDFs are cached
by ``(tex_path, mtime)`` so an ``svg``/``png`` render immediately following a
``pdf`` render of the same source does not recompile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

#: Hard timeout (seconds) for every external tool invocation.  Generous
#: because the very first MiKTeX compile may auto-install packages.
_SUBPROCESS_TIMEOUT = 300

#: Lines of context reported after each ``!`` error line from the LaTeX log.
_LOG_CONTEXT_LINES = 4

#: Upper bound on error-report lines so we never dump a whole log file.
_MAX_ERROR_LINES = 40


class RenderError(RuntimeError):
    """Raised when an external rendering tool fails."""


class Renderer:
    """Detects and drives the local LaTeX toolchain.

    Attributes:
        tools: Mapping of tool name (``pdflatex``, ``pdftocairo``,
            ``dvisvgm``, ``pdftoppm``) to its resolved executable path, or
            ``None`` when the tool is not on ``PATH``.  Populated once at
            construction time; useful for diagnostics.
    """

    _TOOL_NAMES = ("pdflatex", "pdftocairo", "dvisvgm", "pdftoppm")

    def __init__(self) -> None:
        self.tools: Dict[str, Optional[str]] = {
            name: shutil.which(name) for name in self._TOOL_NAMES
        }
        # (abs tex path, mtime_ns, abs output dir) -> abs pdf path
        self._pdf_cache: Dict[Tuple[str, int, str], str] = {}

    # ------------------------------------------------------------------ API

    def available(self) -> bool:
        """True if a LaTeX engine capable of PDF output is on PATH."""
        return bool(self.tools.get("pdflatex"))

    def install_hint(self) -> str:
        """Platform-appropriate instructions for installing a toolchain."""
        if sys.platform.startswith("win"):
            hint = (
                "Install MiKTeX from https://miktex.org - it provides "
                "pdflatex (which can auto-install the circuitikz package on "
                "first use) plus pdftocairo and dvisvgm for SVG and PNG "
                "export."
            )
        elif sys.platform.startswith("linux"):
            hint = (
                "Install TeX Live and Poppler, e.g. on Debian/Ubuntu: "
                "'sudo apt install texlive texlive-pictures poppler-utils', "
                "or on Fedora: 'sudo dnf install texlive texlive-pictures "
                "poppler-utils'."
            )
        else:
            hint = (
                "Install MacTeX from https://www.tug.org/mactex/ (or a TeX "
                "Live distribution) together with Poppler for SVG/PNG "
                "export, e.g. 'brew install --cask mactex' and "
                "'brew install poppler'."
            )
        return (
            hint
            + " Until a toolchain is installed, only the .tex file is "
            "produced."
        )

    def render(self, tex_path: str, fmt: str, output_dir: str) -> str:
        """Compile *tex_path* and return the produced file path.

        *fmt* is ``'pdf'``, ``'svg'`` or ``'png'``.  The PDF is always
        compiled first (into *output_dir*); SVG/PNG are converted from it.
        Raises :class:`RenderError` on failure.
        """
        fmt_normalized = fmt.strip().lower()
        if fmt_normalized not in ("pdf", "svg", "png"):
            raise RenderError(
                f"Unsupported output format '{fmt}' "
                "(expected 'pdf', 'svg' or 'png')."
            )
        if not self.available():
            raise RenderError(
                "pdflatex was not found on PATH. " + self.install_hint()
            )
        tex_abs = os.path.abspath(tex_path)
        if not os.path.isfile(tex_abs):
            raise RenderError(f"LaTeX source file not found: {tex_abs}")
        out_dir = os.path.abspath(output_dir)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            raise RenderError(
                f"Cannot create output directory '{out_dir}': {exc}"
            ) from exc

        pdf_path = self._compile_pdf(tex_abs, out_dir)
        if fmt_normalized == "pdf":
            return pdf_path
        if fmt_normalized == "svg":
            return self._pdf_to_svg(pdf_path)
        return self._pdf_to_png(pdf_path)

    # ------------------------------------------------------------- internals

    def _compile_pdf(self, tex_path: str, output_dir: str) -> str:
        """Compile *tex_path* to a PDF in *output_dir*, with caching."""
        try:
            mtime_ns = os.stat(tex_path).st_mtime_ns
        except OSError as exc:
            raise RenderError(f"Cannot stat '{tex_path}': {exc}") from exc

        cache_key = (tex_path, mtime_ns, output_dir)
        cached = self._pdf_cache.get(cache_key)
        if cached is not None and self._is_nonempty_file(cached):
            return cached

        stem = os.path.splitext(os.path.basename(tex_path))[0]
        pdf_path = os.path.join(output_dir, stem + ".pdf")
        log_path = os.path.join(output_dir, stem + ".log")
        aux_path = os.path.join(output_dir, stem + ".aux")

        pdflatex = self.tools["pdflatex"]
        assert pdflatex is not None  # guarded by available() in render()
        cmd: List[str] = [
            pdflatex,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            output_dir,
        ]
        if "miktex" in pdflatex.lower():
            # Let MiKTeX fetch missing packages (e.g. circuitikz) on the fly.
            cmd.append("--enable-installer")
        cmd.append(tex_path)

        proc = self._run(cmd)
        if proc.returncode != 0:
            raise RenderError(
                "pdflatex failed (exit code "
                f"{proc.returncode}):\n{self._latex_error_report(log_path, proc)}"
            )
        if not self._is_nonempty_file(pdf_path):
            raise RenderError(
                "pdflatex reported success but produced no PDF at "
                f"'{pdf_path}'."
            )

        # Clean auxiliary files only after a successful compile so the log
        # survives for troubleshooting when something went wrong.
        for path in (aux_path, log_path):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass  # leftover aux files are harmless

        self._pdf_cache[cache_key] = pdf_path
        return pdf_path

    def _pdf_to_svg(self, pdf_path: str) -> str:
        """Convert *pdf_path* to an SVG next to it and return the SVG path."""
        svg_path = os.path.splitext(pdf_path)[0] + ".svg"
        pdftocairo = self.tools.get("pdftocairo")
        dvisvgm = self.tools.get("dvisvgm")
        if pdftocairo:
            tool_name = "pdftocairo"
            cmd = [pdftocairo, "-svg", pdf_path, svg_path]
        elif dvisvgm:
            tool_name = "dvisvgm"
            cmd = [dvisvgm, "--pdf", pdf_path, "-o", svg_path]
        else:
            raise RenderError(
                "No SVG converter found on PATH (need pdftocairo or "
                "dvisvgm). " + self.install_hint()
            )
        proc = self._run(cmd)
        if proc.returncode != 0:
            raise RenderError(
                f"{tool_name} failed (exit code {proc.returncode}): "
                + self._tool_output_tail(proc)
            )
        self._require_output(svg_path, tool_name)
        return svg_path

    def _pdf_to_png(self, pdf_path: str) -> str:
        """Convert *pdf_path* to a 300 dpi PNG and return the PNG path."""
        base = os.path.splitext(pdf_path)[0]
        png_path = base + ".png"
        pdftocairo = self.tools.get("pdftocairo")
        pdftoppm = self.tools.get("pdftoppm")
        if pdftocairo:
            tool_name = "pdftocairo"
            cmd = [pdftocairo, "-png", "-r", "300", "-singlefile",
                   pdf_path, base]
        elif pdftoppm:
            tool_name = "pdftoppm"
            cmd = [pdftoppm, "-png", "-r", "300", "-singlefile",
                   pdf_path, base]
        else:
            raise RenderError(
                "No PNG converter found on PATH (need pdftocairo or "
                "pdftoppm). " + self.install_hint()
            )
        proc = self._run(cmd)
        if proc.returncode != 0:
            raise RenderError(
                f"{tool_name} failed (exit code {proc.returncode}): "
                + self._tool_output_tail(proc)
            )
        if not os.path.isfile(png_path):
            # Some pdftoppm builds append a page number despite -singlefile.
            for suffix in ("-1", "-01", "-001"):
                candidate = base + suffix + ".png"
                if os.path.isfile(candidate):
                    try:
                        os.replace(candidate, png_path)
                    except OSError as exc:
                        raise RenderError(
                            f"Could not rename '{candidate}' to "
                            f"'{png_path}': {exc}"
                        ) from exc
                    break
        self._require_output(png_path, tool_name)
        return png_path

    def _run(self, cmd: List[str]) -> "subprocess.CompletedProcess[str]":
        """Run *cmd* (explicit argv, no shell) capturing text output."""
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(
                f"{os.path.basename(cmd[0])} timed out after "
                f"{_SUBPROCESS_TIMEOUT} seconds."
            ) from exc
        except OSError as exc:
            raise RenderError(
                f"Could not run {os.path.basename(cmd[0])}: {exc}"
            ) from exc

    @staticmethod
    def _latex_error_report(
        log_path: str, proc: "subprocess.CompletedProcess[str]"
    ) -> str:
        """Extract the '!' error lines (plus context) from the LaTeX log.

        Falls back to the tail of pdflatex's stdout when the log file is
        missing or contains no error markers.  Never returns the whole log.
        """
        log_lines: List[str] = []
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                log_lines = fh.read().splitlines()
        except OSError:
            log_lines = []

        picked: List[str] = []
        index = 0
        while index < len(log_lines) and len(picked) < _MAX_ERROR_LINES:
            if log_lines[index].startswith("!"):
                picked.extend(
                    log_lines[index:index + 1 + _LOG_CONTEXT_LINES]
                )
                index += 1 + _LOG_CONTEXT_LINES
            else:
                index += 1
        if picked:
            return "\n".join(picked[:_MAX_ERROR_LINES])

        stdout_tail = (proc.stdout or "").strip().splitlines()[-10:]
        if stdout_tail:
            return "\n".join(stdout_tail)
        return "(no diagnostic output captured)"

    @staticmethod
    def _tool_output_tail(proc: "subprocess.CompletedProcess[str]") -> str:
        """Short tail of a converter's stderr (or stdout) for error text."""
        text = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        tail = text.splitlines()[-8:]
        return "\n".join(tail) if tail else "(no diagnostic output captured)"

    @staticmethod
    def _is_nonempty_file(path: str) -> bool:
        """True when *path* exists and has non-zero size."""
        try:
            return os.path.getsize(path) > 0
        except OSError:
            return False

    def _require_output(self, path: str, tool_name: str) -> None:
        """Raise RenderError unless *path* exists and is non-empty."""
        if not self._is_nonempty_file(path):
            raise RenderError(
                f"{tool_name} reported success but '{path}' is missing or "
                "empty."
            )
