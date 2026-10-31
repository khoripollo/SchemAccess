"""Units after values on the drawing (--units)."""

from __future__ import annotations

import pytest

from test_circuitikz import drawing_of

from schemaccess import circuitikz, cli, units
from schemaccess.model import ComponentType as T, FieldText
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


@pytest.mark.parametrize("value,kind,expected", [
    ("100", T.RESISTOR, r"\SI{100}{\ohm}"),
    ("4k7", T.RESISTOR, r"\SI{4.7}{\kilo\ohm}"),
    ("1M", T.RESISTOR, r"\SI{1}{\mega\ohm}"),
    ("1uF", T.CAPACITOR, r"\SI{1}{\micro\farad}"),
    ("22n", T.CAPACITOR, r"\SI{22}{\nano\farad}"),
    ("10m", T.INDUCTOR, r"\SI{10}{\milli\henry}"),
    ("12V", T.VOLTAGE_SOURCE, r"\SI{12}{\volt}"),
    ("2m", T.CURRENT_SOURCE, r"\SI{2}{\milli\ampere}"),
    # Not quantities: a part number, a phasor, a blank.
    ("1N4148", T.DIODE, None),
    ("5V 1kHz", T.AC_SOURCE, None),
    ("~", T.CAPACITOR, None),
])
def test_siunitx(value: str, kind, expected) -> None:
    assert units.siunitx(value, kind) == expected


def test_drawing_writes_values_as_siunitx_quantities(fixtures_dir,
                                                     tmp_path) -> None:
    """Values are typeset as quantities, so the units come out upright.

    circuitikz is loaded with its siunitx option, so a value becomes
    ``\\SI{4.7}{\\kilo\\ohm}`` rather than hand-set characters.
    """
    path = str(fixtures_dir / "loops_grid.kicad_sch")
    code = run_pipeline(
        PipelineOptions(path, str(tmp_path), dry_run=True)).tikz_code
    assert r"\usepackage[siunitx, american, RPvoltages]{circuitikz}" in code
    assert r"\usepackage{amssymb}" in code
    assert r"\SI{12}{\volt}" in drawing_of(code, "V1")
    assert r"\SI{100}{\ohm}" in drawing_of(code, "R1")


def test_every_part_carries_its_unit(fixtures_dir, tmp_path) -> None:
    """Resistors are drawn like every other part.

    Regression: resistors used to be the only parts whose unit did not
    follow the same rule as the rest - the writer appended an ohm sign
    itself, so they carried one whatever the caller asked for while
    capacitors and inductors did not.  Now a value is a quantity or it
    is not, and rc_divider holds one of each: bare resistors, a
    prefixed capacitor and a bare source.
    """
    path = str(fixtures_dir / "rc_divider.kicad_sch")
    code = run_pipeline(
        PipelineOptions(path, str(tmp_path), dry_run=True)).tikz_code
    assert r"\SI{20}{\ohm}" in drawing_of(code, "R1")         # written "20"
    assert r"\SI{100}{\ohm}" in drawing_of(code, "R2")        # "100"
    assert r"\SI{22}{\nano\farad}" in drawing_of(code, "C1")  # "22n"
    assert r"\SI{5}{\volt}" in drawing_of(code, "V1")         # "5"


def test_cli_runs_without_a_units_flag(fixtures_dir) -> None:
    """Units are no longer optional, so the flag is gone."""
    assert cli.main([str(fixtures_dir / "loops_two.kicad_sch"), "--check",
                     "--quiet"]) == 0
    with pytest.raises(SystemExit):
        cli.main([str(fixtures_dir / "loops_two.kicad_sch"), "--units"])


@pytest.mark.parametrize("name", ["rc_divider.kicad_sch",
                                  "rlc_series.kicad_sch",
                                  "loops_grid.kicad_sch"])
def test_labels_land_on_their_schematic_coordinate(name: str, load) -> None:
    """A drawn field is set exactly where KiCad has it, not beside it.

    This is the whole point of reading the position: the author drags a
    reference or a value to wherever it reads best, and the drawing
    cannot reproduce that from a choice of two slots.
    """
    graph = load(name)
    tex = circuitikz.generate(graph)
    transform = circuitikz._Transform(graph)
    checked = 0
    for ref, comp in graph.components.items():
        if len(comp.pins) != 2:
            continue
        drawing = drawing_of(tex, ref)
        for field, text in (("Reference", ref),
                            ("Value", circuitikz._format_value(comp))):
            where = comp.field_text.get(field)
            if not text or not comp.shows(field) or where is None:
                continue
            at = circuitikz._xy(*transform.point(where.position))
            assert f"at {at} {{{text}}};" in drawing, \
                f"{ref} {field} is not at {at}:\n{drawing}"
            checked += 1
    assert checked, f"{name} drew no positioned fields"


class _Identity:
    """A transform that leaves points alone, so a test can state label
    positions in the coordinates it then asserts on."""

    @staticmethod
    def point(p):
        return p


def _part(load, **fields):
    comp = load("rc_divider.kicad_sch").components["R1"]
    comp.angle, comp.mirror = 0.0, ""
    comp.field_text.clear()
    comp.field_text.update(fields)
    return comp


def test_both_fields_can_sit_on_the_same_side(load) -> None:
    """KiCad stacks a reference over a value, and so does the drawing.

    Regression: the two used to go in circuitikz's ``l`` and ``a``
    slots, which sit on opposite sides of the part whatever the
    schematic does, so a value the author had put above a resistor came
    out below it and only moved further away when the reference moved.
    """
    comp = _part(load, Reference=FieldText(0.0, 2.0),
                 Value=FieldText(0.0, 1.0))
    options, nodes = circuitikz._bipole_labels(comp, _Identity)
    assert options == ""
    assert nodes == [r"\node[anchor=center] at (0,2) {R1};",
                     r"\node[anchor=center] at (0,1) {\SI{20}{\ohm}};"]


def test_a_hidden_field_is_not_drawn_at_its_position(load) -> None:
    """A recorded position is not a reason to draw a field KiCad hides."""
    comp = _part(load, Reference=FieldText(0.0, 2.0),
                 Value=FieldText(0.0, 1.0))
    comp.hidden_properties.add("Reference")
    _options, nodes = circuitikz._bipole_labels(comp, _Identity)
    assert nodes == [r"\node[anchor=center] at (0,1) {\SI{20}{\ohm}};"]


def test_labels_fall_back_to_the_slots_without_a_position(load) -> None:
    """A component built in code has no text to follow, so circuitikz
    places the labels itself."""
    options, nodes = circuitikz._bipole_labels(_part(load), _Identity)
    assert options == r", l={R1}, a={\SI{20}{\ohm}}"
    assert nodes == []


@pytest.mark.parametrize("angle,mirror,field,expected", [
    # Upright and unmirrored: KiCad's justification carries straight over.
    (0, "", FieldText(0, 0, 0, ("left",)), (0, "left", "")),
    # KiCad never sets text upside down, so half a turn comes back as no
    # turn with the ends swapped.  This is why a resistor rotated 180
    # degrees stores a right-justified value and still draws it to the
    # right of the part.
    (180, "", FieldText(0, 0, 0, ("right",)), (0, "left", "")),
    # A quarter turn on the symbol and another on the field cancel, which
    # is how a rotated resistor keeps its reference level.
    (90, "", FieldText(0, 0, 90, ()), (0, "", "")),
    # A quarter turn on its own stands.
    (90, "", FieldText(0, 0, 0, ("left",)), (90, "left", "")),
    # Mirroring swaps the two ends of a text it runs along...
    (0, "y", FieldText(0, 0, 0, ("left",)), (0, "right", "")),
    # ...and the top and bottom of one it runs across.
    (0, "x", FieldText(0, 0, 0, ("left", "top")), (0, "left", "bottom")),
])
def test_text_frame_undoes_kicads_symbol_transform(angle, mirror, field,
                                                   expected, load) -> None:
    """A field's angle and justification are stored in its symbol's own
    frame, so neither can be used before the symbol's placement is
    applied to it."""
    comp = _part(load)
    comp.angle, comp.mirror = angle, mirror
    assert circuitikz._text_frame(comp, field) == expected


def test_drawing_is_deterministic(load) -> None:
    """The same schematic draws identically every time (REL-1)."""
    assert circuitikz.generate(load("rlc_series.kicad_sch")) == \
        circuitikz.generate(load("rlc_series.kicad_sch"))


def test_units_reach_the_drawing_without_the_pipeline(load) -> None:
    """The writer formats from the raw value, not from a pre-pass.

    ``units.apply`` still exists for the renderers that draw text
    themselves, but the LaTeX writer must not depend on it having run.
    """
    code = circuitikz.generate(load("rlc_series.kicad_sch"))
    assert r"\SI{10}{\milli\henry}" in code
