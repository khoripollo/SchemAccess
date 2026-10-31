"""Tests for the netlist comparison itself.

:mod:`schemaccess.netcompare` is what decides whether SchemAccess and
KiCad agree, so it needs testing from both sides: it must pass circuits
that are the same written differently, and it must fail circuits that are
genuinely wired differently.  A comparison that only ever passes would
make ``test_kicad_reference.py`` worthless.

These run on netlists written out by hand, so they need no KiCad.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import pytest

from schemaccess import netcompare

Pin = Tuple[str, str]


def net_file(components: Dict[str, str],
             nets: Dict[str, Sequence[Pin]]) -> str:
    """A KiCad-format netlist from ``{ref: value}`` and ``{net: pins}``."""
    lines = ['(export (version "E")', "  (design",
             '    (source "t.kicad_sch")', '    (tool "test"))',
             "  (components"]
    for ref, value in components.items():
        lines.append(f'    (comp (ref "{ref}") (value "{value}")')
        lines.append(f'      (libsource (lib "Device") '
                     f'(part "{ref[0]}")))')
    lines.extend(["  )", "  (nets"])
    for code, (name, pins) in enumerate(nets.items(), start=1):
        lines.append(f'    (net (code "{code}") (name "{name}")')
        for ref, number in pins:
            lines.append(f'      (node (ref "{ref}") (pin "{number}") '
                         f'(pintype "passive"))')
        lines.append("    )")
    lines.extend(["  )", ")"])
    return "\n".join(lines) + "\n"


# A plain divider: V1 across R1 and R2, the midpoint shared, bottom to
# ground.  Written once per side with different net names on purpose.
DIVIDER_PARTS = {"V1": "5", "R1": "10k", "R2": "10k"}
_REFERENCE_NETS: Dict[str, List[Pin]] = {
    "Net-(R1-Pad1)": [("V1", "1"), ("R1", "1")],
    "Net-(R1-Pad2)": [("R1", "2"), ("R2", "1")],
    "GND": [("R2", "2"), ("V1", "2")],
}
_GENERATED_NETS: Dict[str, List[Pin]] = {
    "N1": [("R1", "1"), ("V1", "1")],
    "N2": [("R2", "1"), ("R1", "2")],
    "GND": [("V1", "2"), ("R2", "2")],
}


def divider(swap: Dict[str, List[Pin]] = None,
            parts: Dict[str, str] = None, *,
            generated: bool = False) -> str:
    nets = dict(_GENERATED_NETS if generated else _REFERENCE_NETS)
    nets.update(swap or {})
    return net_file(parts or DIVIDER_PARTS, nets)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("left,right", [
    ("10k", "10k"),
    ("10k", "10000"),
    ("22nF", "22n"),
    ("100 Ohm", "100"),
    ("4k7", "4.7k"),
    ("10mH", "10m"),
    ("1Meg", "1000000"),
    ("1N914", "1N914"),
    ("~", ""),
    ("", "?"),
    ("10K", "10k"),
])
def test_values_that_mean_the_same_thing_agree(left, right) -> None:
    assert netcompare.values_agree(left, right)
    assert netcompare.values_agree(right, left)


@pytest.mark.parametrize("left,right", [
    ("10k", "10"),
    ("22nF", "22uF"),
    ("10k", ""),
    ("1N914", "1N4148"),
    ("LED", "D"),
])
def test_values_that_differ_do_not_agree(left, right) -> None:
    assert not netcompare.values_agree(left, right)


def test_unexpanded_text_variable_is_not_a_value() -> None:
    """``${SIM.PARAMS}`` names a variable; it is not a value to compare."""
    assert netcompare.is_placeholder("${SIM.PARAMS}")
    assert not netcompare.is_placeholder("10k")
    assert not netcompare.is_placeholder("")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_kicad_netlist_reads_parts_and_pins() -> None:
    parsed = netcompare.parse_kicad_netlist(divider())
    assert set(parsed.parts) == {"V1", "R1", "R2"}
    assert parsed.parts["R1"].value == "10k"
    assert parsed.parts["R1"].part == "R"
    assert parsed.pins == {("V1", "1"), ("V1", "2"), ("R1", "1"),
                           ("R1", "2"), ("R2", "1"), ("R2", "2")}
    assert parsed.ground_pins() == frozenset({("R2", "2"), ("V1", "2")})


def test_parse_kicad_netlist_drops_power_symbols() -> None:
    """A power flag is a net name, not a component on the net."""
    text = net_file({"R1": "1k", "#PWR01": "GND"},
                    {"GND": [("R1", "1"), ("#PWR01", "1")]})
    parsed = netcompare.parse_kicad_netlist(text)
    assert set(parsed.parts) == {"R1"}
    assert parsed.nets[0].pins == frozenset({("R1", "1")})


def test_parse_kicad_netlist_rejects_other_files() -> None:
    with pytest.raises(netcompare.NetlistFormatError):
        netcompare.parse_kicad_netlist('(kicad_sch (version 20231120))')
    with pytest.raises(netcompare.NetlistFormatError):
        netcompare.parse_kicad_netlist("not an s-expression at all")


def test_parse_spice_deck_splits_nodes_from_the_rest() -> None:
    deck = netcompare.parse_spice_deck("""\
* a comment
R1 a b 10k
C1 b 0 22n
Q1 c base e MODEL
XU1 inp inn out SCHEMACCESS_OPAMP
.model MODEL NPN
.subckt SCHEMACCESS_OPAMP inp inn out
Egain out 0 inp inn 1e6
.ends SCHEMACCESS_OPAMP
.end
""")
    assert set(deck.devices) == {"R1", "C1", "Q1", "XU1"}
    assert deck.devices["R1"].nodes == ("a", "b")
    assert deck.devices["R1"].args == ("10k",)
    assert deck.devices["Q1"].nodes == ("c", "base", "e")
    assert deck.devices["XU1"].nodes == ("inp", "inn", "out")
    assert deck.devices["XU1"].args == ("SCHEMACCESS_OPAMP",)
    assert deck.models == {"MODEL": "NPN"}
    # Egain lives inside the subcircuit, which is library code.
    assert "Egain" not in deck.devices


def test_parse_spice_deck_records_lines_it_cannot_use() -> None:
    """KiCad writes ``<ref> __<ref>`` for a symbol it cannot map."""
    deck = netcompare.parse_spice_deck("D1 __D1\nU1 __U1\nR1 a b 1k\n")
    assert set(deck.devices) == {"R1"}
    assert "D1" in deck.incomplete and "needs 2 node" in deck.incomplete["D1"]
    assert "U1" in deck.incomplete
    assert "not a SPICE device letter" in deck.incomplete["U1"]


def test_parse_spice_deck_notices_a_deliberate_omission() -> None:
    deck = netcompare.parse_spice_deck(
        "* SW1 (switch) needs a control node SPICE cannot infer\n"
        "*   pins: 1=a  2=b\n")
    assert deck.commented == {"SW1"}


# ---------------------------------------------------------------------------
# Comparing KiCad netlists
# ---------------------------------------------------------------------------

def test_same_circuit_under_different_net_names_passes() -> None:
    """The whole point: names differ, pins do not."""
    report = netcompare.compare_kicad_netlists(divider(),
                                               divider(generated=True))
    assert report.ok, report.format()
    assert report.verdict == "PASS"
    assert "3 net(s) joining the same pins on both sides" in report.checks


def test_splitting_a_net_is_caught() -> None:
    report = netcompare.compare_kicad_netlists(
        divider(),
        divider(generated=True,
                swap={"N2": [("R1", "2")], "N4": [("R2", "1")]}))
    assert not report.ok
    message = report.format()
    assert "splits them across 2 nets" in message
    assert "R1 pin 2" in message and "R2 pin 1" in message


def test_merging_two_nets_is_caught() -> None:
    report = netcompare.compare_kicad_netlists(
        divider(),
        divider(generated=True,
                swap={"N1": [("R1", "1"), ("V1", "1"), ("R1", "2"),
                             ("R2", "1")],
                      "N2": []}))
    assert not report.ok
    assert "also joins" in report.format()


def test_a_missing_component_is_caught() -> None:
    parts = dict(DIVIDER_PARTS)
    del parts["R2"]
    report = netcompare.compare_kicad_netlists(divider(),
                                               divider(generated=True,
                                                       parts=parts))
    assert not report.ok
    assert any(diff.area == "components" and "R2" in diff.message
               for diff in report.differences), report.format()


def test_a_changed_value_is_caught() -> None:
    report = netcompare.compare_kicad_netlists(
        divider(),
        divider(generated=True, parts={**DIVIDER_PARTS, "R2": "47k"}))
    assert not report.ok
    assert any(diff.area == "values" and "R2" in diff.message
               for diff in report.differences), report.format()


def test_ground_on_the_wrong_net_is_caught() -> None:
    """Both sides name a ground net, but not the same one."""
    report = netcompare.compare_kicad_netlists(
        divider(),
        divider(generated=True,
                swap={"GND": [("R1", "1"), ("V1", "1")],
                      "N1": [("V1", "2"), ("R2", "2")]}))
    assert not report.ok
    assert any(diff.area == "ground" for diff in report.differences), \
        report.format()


def test_ground_is_not_checked_when_the_reference_does_not_name_it() -> None:
    """KiCad leaves a net anonymous when the ground symbol has no value.

    Silence about where ground is cannot contradict SchemAccess, so this
    has to be a note rather than a difference.
    """
    anonymous = dict(_REFERENCE_NETS)
    anonymous["Net-(R2-Pad2)"] = anonymous.pop("GND")
    report = netcompare.compare_kicad_netlists(
        net_file(DIVIDER_PARTS, anonymous), divider(generated=True))
    assert report.ok, report.format()
    assert any("names no ground net" in note for note in report.notes)


def test_a_pin_alone_on_a_net_is_only_a_note() -> None:
    """A hidden no-connect pin joins nothing, so leaving it out is fine."""
    with_nc = dict(_REFERENCE_NETS)
    with_nc["unconnected-(R1-NC-Pad3)"] = [("R1", "3")]
    report = netcompare.compare_kicad_netlists(
        net_file(DIVIDER_PARTS, with_nc), divider(generated=True))
    assert report.ok, report.format()
    assert any("R1 pin 3" in note for note in report.notes)


def test_a_pin_lost_from_a_shared_net_is_a_difference() -> None:
    """The same pin missing from a net that joins others is wiring lost."""
    report = netcompare.compare_kicad_netlists(
        divider(),
        divider(generated=True, swap={"N2": [("R1", "2")]}))
    assert not report.ok
    assert any("R2 pin 1" in diff.message for diff in report.differences), \
        report.format()


# ---------------------------------------------------------------------------
# Comparing SPICE decks
# ---------------------------------------------------------------------------

_REFERENCE_DECK = """\
.title KiCad schematic
V1 Net-_R1-Pad1_ GND DC 5
R1 Net-_R1-Pad1_ Net-_R1-Pad2_ 10k
R2 Net-_R1-Pad2_ GND 10k
.end
"""

_GENERATED_DECK = """\
* t.kicad_sch - netlist generated by SchemAccess
V1 N1 0 DC 5
R1 N1 N2 10k
R2 N2 0 10k
.end
"""


def test_consistently_renamed_nodes_pass() -> None:
    report = netcompare.compare_spice_decks(_REFERENCE_DECK, _GENERATED_DECK)
    assert report.ok, report.format()
    assert "3 device(s) with matching node counts, node order and values" \
        in report.checks
    assert any("node 0" in check for check in report.checks)


def test_swapped_nodes_break_the_mapping() -> None:
    """R2 wired the other way round cannot be a consistent renaming."""
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK, _GENERATED_DECK.replace("R2 N2 0", "R2 0 N2"))
    assert not report.ok
    assert any(diff.area == "topology" and "R2" in diff.message
               for diff in report.differences), report.format()


def test_two_nets_merged_into_one_node_is_caught() -> None:
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK, _GENERATED_DECK.replace("R1 N1 N2", "R1 N1 N1"))
    assert not report.ok
    assert any(diff.area == "topology" for diff in report.differences), \
        report.format()


def test_ground_must_become_spice_node_zero() -> None:
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK, _GENERATED_DECK.replace("0", "GNDNODE"))
    assert not report.ok
    assert any(diff.area == "ground" for diff in report.differences), \
        report.format()


def test_a_wrong_value_is_caught() -> None:
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK, _GENERATED_DECK.replace("R2 N2 0 10k",
                                                 "R2 N2 0 100k"))
    assert not report.ok
    assert any(diff.area == "values" and "R2" in diff.message
               for diff in report.differences), report.format()


def test_unit_conversion_still_counts_as_the_same_value() -> None:
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK.replace("10k", "10000"), _GENERATED_DECK)
    assert report.ok, report.format()


def test_a_device_kicad_could_not_write_is_only_a_note() -> None:
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK.replace("R2 Net-_R1-Pad2_ GND 10k", "R2 __R2"),
        _GENERATED_DECK)
    assert report.ok, report.format()
    assert any("no usable device line" in note for note in report.notes)


def test_a_device_schemaccess_explains_leaving_out_is_only_a_note() -> None:
    """SchemAccess comments out what it refuses to guess at, and says so."""
    reference = _REFERENCE_DECK.replace(
        ".end", "SW1 Net-_R1-Pad2_ GND CTRLP CTRLM SWMODEL\n.end")
    generated = _GENERATED_DECK.replace(
        ".end", "* SW1 (switch) needs a control node SPICE cannot infer\n"
                ".end")
    report = netcompare.compare_spice_decks(reference, generated)
    assert report.ok, report.format()
    assert any("on purpose" in note for note in report.notes)


def test_a_device_dropped_without_explanation_is_a_difference() -> None:
    reference = _REFERENCE_DECK.replace(
        ".end", "SW1 Net-_R1-Pad2_ GND CTRLP CTRLM SWMODEL\n.end")
    report = netcompare.compare_spice_decks(reference, _GENERATED_DECK)
    assert not report.ok
    assert any(diff.area == "devices" and "SW1" in diff.message
               for diff in report.differences), report.format()


def test_a_device_schemaccess_invented_is_a_difference() -> None:
    report = netcompare.compare_spice_decks(
        _REFERENCE_DECK, _GENERATED_DECK.replace(".end", "R9 N2 0 1k\n.end"))
    assert not report.ok
    assert any("R9" in diff.message for diff in report.differences), \
        report.format()


def test_a_prefixed_device_name_still_matches() -> None:
    """SPICE needs an op amp called U1 to be the subcircuit call XU1.

    KiCad cannot write a device whose name starts with U at all, so it
    writes the placeholder ``U1 __U1``.  Pairing that with SchemAccess's
    ``XU1`` is what keeps it a note about KiCad's export rather than a
    complaint that SchemAccess invented a device.
    """
    reference = ".title t\nU1 __U1\nR1 a b 1k\n.end\n"
    generated = (".title t\nXU1 p q r SCHEMACCESS_OPAMP\n"
                 "R1 p q 1k\n.end\n")
    report = netcompare.compare_spice_decks(reference, generated)
    assert report.ok, report.format()
    assert any("U1" in note and "no usable device line" in note
               for note in report.notes), report.format()
    assert not any("XU1" in diff.message
                   for diff in report.differences), report.format()


def test_different_subcircuits_are_not_compared_node_by_node() -> None:
    """KiCad's op-amp model takes its supply rails; the ideal one does not."""
    reference = (".title t\nXU1 inp inn out vp vn OPAMP_KICAD\n"
                 "R1 inp inn 1k\n.end\n")
    generated = (".title t\nXU1 a b c SCHEMACCESS_OPAMP\n"
                 "R1 a b 1k\n.end\n")
    report = netcompare.compare_spice_decks(reference, generated)
    assert report.ok, report.format()
    assert any("different models" in note for note in report.notes)


def test_kicads_placeholder_for_an_unnamed_net_is_not_a_node() -> None:
    """KiCad writes ``_NO_NET_`` for *every* net it could not name."""
    reference = (".title t\nR1 /_NO_NET_ GND 1k\nR2 /_NO_NET_ GND 2k\n"
                 ".end\n")
    generated = (".title t\nR1 N1 0 1k\nR2 N2 0 2k\n.end\n")
    report = netcompare.compare_spice_decks(reference, generated)
    assert report.ok, report.format()
    assert any("_NO_NET_" in note for note in report.notes)


# ---------------------------------------------------------------------------
# Dispatch and files
# ---------------------------------------------------------------------------

def test_kind_is_taken_from_the_extension() -> None:
    assert netcompare.kind_of_path("board.net") == netcompare.KIND_KICAD
    assert netcompare.kind_of_path("board.cir") == netcompare.KIND_SPICE
    assert netcompare.kind_of_path("board.kicad_sch") is None


def test_compare_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        netcompare.compare("csv", "", "")


def test_compare_files_reads_both_sides(tmp_path) -> None:
    reference = tmp_path / "ref.net"
    generated = tmp_path / "mine.net"
    reference.write_text(divider(), encoding="utf-8")
    generated.write_text(divider(generated=True), encoding="utf-8")

    report = netcompare.compare_files(str(reference), str(generated))
    assert report.ok, report.format()
    assert report.subject == "ref.net"


def test_compare_files_needs_a_recognisable_extension(tmp_path) -> None:
    unknown = tmp_path / "ref.txt"
    unknown.write_text(divider(), encoding="utf-8")
    with pytest.raises(ValueError):
        netcompare.compare_files(str(unknown), str(unknown))


def test_report_formats_a_verdict_and_the_differences() -> None:
    report = netcompare.compare_kicad_netlists(
        divider(),
        divider(generated=True, parts={**DIVIDER_PARTS, "R2": "47k"}),
        subject="board.net")
    text = report.format()
    assert text.startswith("FAIL  board.net")
    assert "KiCad reference vs SchemAccess" in text
    assert "differences (1):" in text
    assert "R2" in text
