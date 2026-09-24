"""Units after values on the drawing (--units)."""

from __future__ import annotations

import pytest

from schemaccess import circuitikz, cli, units
from schemaccess.model import ComponentType as T
from schemaccess.pipeline import PipelineOptions, run_pipeline


@pytest.mark.parametrize("value,kind,expected", [
    ("1", T.INDUCTOR, "1 H"),
    ("10m", T.INDUCTOR, "10 mH"),
    ("22n", T.CAPACITOR, "22 nF"),
    ("22nF", T.CAPACITOR, "22 nF"),
    ("1u", T.CAPACITOR, "1 µF"),
    ("4n7", T.CAPACITOR, "4.7 nF"),
    ("100", T.RESISTOR, "100 Ω"),
    ("4k7", T.RESISTOR, "4.7 kΩ"),
    ("1M", T.RESISTOR, "1 MΩ"),
    ("100 Ohm", T.RESISTOR, "100 Ω"),
    ("5", T.VOLTAGE_SOURCE, "5 V"),
    ("9V", T.BATTERY, "9 V"),
    ("2m", T.CURRENT_SOURCE, "2 mA"),
    ("5V 1kHz", T.AC_SOURCE, "5V 1kHz"),
    ("R", T.RESISTOR, "R"),
    ("~", T.CAPACITOR, "~"),
    ("10V", T.CAPACITOR, "10V"),
    ("1N4148", T.DIODE, "1N4148"),
])
def test_with_unit(value: str, kind, expected: str) -> None:
    assert units.with_unit(value, kind) == expected


def test_apply_leaves_the_circuit_alone(load) -> None:
    graph = load("rlc_series.kicad_sch")
    drawn = units.apply(graph)
    assert drawn.components["L1"].value == "10 mH"
    assert graph.components["L1"].value == "10mH"
    assert drawn.nets is graph.nets


def test_drawing_shows_units(fixtures_dir, tmp_path) -> None:
    path = str(fixtures_dir / "loops_grid.kicad_sch")
    plain = run_pipeline(PipelineOptions(path, str(tmp_path), dry_run=True))
    shown = run_pipeline(PipelineOptions(path, str(tmp_path), dry_run=True,
                                         show_units=True))
    assert "a={12V}" in plain.tikz_code and "a={2k2}" in plain.tikz_code
    assert "a={12 V}" in shown.tikz_code
    assert "a={2.2 k$\\Omega$}" in shown.tikz_code
    assert plain.alt_text == shown.alt_text


def test_cli_flag(fixtures_dir) -> None:
    assert cli.main([str(fixtures_dir / "loops_two.kicad_sch"), "--check",
                     "--units", "--quiet"]) == 0


def test_units_off_by_default(load) -> None:
    graph = load("rlc_series.kicad_sch")
    assert circuitikz.generate(graph) == circuitikz.generate(
        load("rlc_series.kicad_sch"))
