"""I/O-2 and I/O-3: the end-to-end pipeline.

* run_pipeline writes the .tex and alt-text files and reports ok on a
  representative set of fixtures (renderer stubbed out so the test is
  fast and toolchain-independent);
* with the real toolchain, format 'all' produces non-empty PDF, SVG and
  PNG for rc_divider (slow);
* malformed input is reported through ``result.errors``, never raised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import LATEX_AVAILABLE
from schemaccess import renderer
from schemaccess.pipeline import PipelineOptions, run_pipeline

_PIPELINE_FIXTURES = ("rc_divider.kicad_sch", "voltage_divider.kicad_sch",
                      "rc_filter.kicad_sch", "led_battery.kicad_sch",
                      "logic_gates.kicad_sch")


@pytest.mark.parametrize("name", _PIPELINE_FIXTURES)
def test_io2_io3_end_to_end_writes_tex_and_alt_text(
        name: str, fixtures_dir, tmp_path, monkeypatch) -> None:
    """The pipeline writes a .tex and an alt-text file and reports ok."""
    # Stub out the LaTeX toolchain so this test stays fast everywhere;
    # real rendering is covered by the slow test below.
    monkeypatch.setattr(renderer.Renderer, "available",
                        lambda self: False)
    stages: list = []
    options = PipelineOptions(input_path=str(fixtures_dir / name),
                              output_dir=str(tmp_path))
    result = run_pipeline(options, progress=stages.append)

    assert result.ok, result.errors
    stem = Path(name).stem

    tex_path = Path(result.output_files["tex"])
    assert tex_path == tmp_path / f"{stem}.tex"
    assert tex_path.is_file() and tex_path.stat().st_size > 0

    alt_path = Path(result.output_files["alt_text"])
    assert alt_path == tmp_path / f"{stem}_alt_text.txt"
    assert alt_path.is_file() and alt_path.stat().st_size > 0

    assert result.tikz_code
    assert result.alt_text
    assert alt_path.read_text(encoding="utf-8").strip() == result.alt_text
    assert "Done." in stages


@pytest.mark.slow
@pytest.mark.skipif(not LATEX_AVAILABLE,
                    reason="pdflatex is not available on PATH")
def test_io2_format_all_produces_pdf_svg_png(fixtures_dir,
                                             tmp_path) -> None:
    """Export format 'all' renders existing, non-empty pdf+svg+png."""
    options = PipelineOptions(
        input_path=str(fixtures_dir / "rc_divider.kicad_sch"),
        output_dir=str(tmp_path),
        export_format="all")
    result = run_pipeline(options)
    assert result.ok, result.errors
    for fmt in ("pdf", "svg", "png"):
        assert fmt in result.output_files, f"missing {fmt} output"
        out = Path(result.output_files[fmt])
        assert out.is_file(), f"{fmt} file was not produced"
        assert out.stat().st_size > 0, f"{fmt} file is empty"


def test_io2_malformed_input_reports_errors_not_exception(
        fixtures_dir, tmp_path) -> None:
    """Bad input surfaces as result.errors, never as an exception."""
    options = PipelineOptions(
        input_path=str(fixtures_dir / "malformed.kicad_sch"),
        output_dir=str(tmp_path))
    result = run_pipeline(options)   # must not raise
    assert not result.ok
    assert result.errors
    assert result.graph is None
