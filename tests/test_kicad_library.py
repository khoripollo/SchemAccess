"""FUN-1: the classifier against KiCad's own shipped symbol libraries.

Every other classification test uses a fixture we wrote.  This one reads
the real ``.kicad_sym`` files KiCad installs and checks that each symbol
lands in the right internal type, using KiCad's own keywords and
descriptions as the oracle.  That is what stops a regression like
"P-MOSFET classified as N-channel" or "every Schottky reported as a plain
diode" from reaching a student.

The whole module is skipped when KiCad is not installed, so the suite
still runs on a machine that only has Python.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import pytest

from schemaccess import sexpr
from schemaccess.kicad_parser import _parse_lib_symbol
from schemaccess.model import ComponentType, SchematicDocument, classify

#: Where KiCad 10 installs its symbol libraries on Windows.
_SEARCH = [
    Path(r"C:\Program Files\KiCad\10.0\share\kicad\symbols"),
    Path(r"C:\Program Files\KiCad\9.0\share\kicad\symbols"),
    Path("/usr/share/kicad/symbols"),
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
]

SYMBOLS = next((p for p in _SEARCH if p.is_dir()), None)

pytestmark = pytest.mark.skipif(
    SYMBOLS is None, reason="KiCad symbol libraries are not installed")


def _load(library: str) -> Dict[str, object]:
    """Parse one .kicad_sym into {symbol name: LibSymbol}.

    Two passes, because a symbol may inherit from one defined later in
    the file: the first pass registers every symbol, the second re-reads
    them with the whole library available to resolve ``extends``.
    """
    single = SYMBOLS / f"{library}.kicad_sym"
    files = ([single] if single.is_file() else
             sorted((SYMBOLS / f"{library}.kicad_symdir").glob("*.kicad_sym")))
    nodes = [n for f in files for n in sexpr.load(str(f))
             if isinstance(n, list) and n and n[0] == "symbol"]
    doc = SchematicDocument()
    for _ in range(2):
        for node in nodes:
            lib = _parse_lib_symbol(node, doc)
            if lib is not None:
                doc.lib_symbols[str(node[1])] = lib
    return doc.lib_symbols


_CACHE: Dict[str, Dict[str, object]] = {}


def library(name: str) -> Dict[str, object]:
    if name not in _CACHE:
        _CACHE[name] = _load(name)
    return _CACHE[name]


def classified(lib_name: str) -> Iterator[Tuple[str, ComponentType, str]]:
    """Yield (symbol name, classified type, keywords+description)."""
    for name, lib in library(lib_name).items():
        pins = lib.pins_for_unit(1) or lib.pins
        ctype = classify(f"{lib_name}:{name}", lib.reference_prefix, "",
                         len(pins), [p.name for p in pins], lib.hints)
        yield name, ctype, f"{name} {lib.hints}"


def _matching(lib_name: str, pattern: str) -> List[Tuple[str, ComponentType]]:
    """Symbols whose own keywords/description match *pattern*."""
    rx = re.compile(pattern, re.IGNORECASE)
    return [(name, ctype) for name, ctype, text in classified(lib_name)
            if rx.search(text)]


def _assert_all(found, expected: ComponentType, label: str,
                limit: int = 8) -> None:
    assert found, f"no symbols found for {label} - the probe itself is wrong"
    wrong = [(n, c.value) for n, c in found if c is not expected]
    assert not wrong, (
        f"{len(wrong)} of {len(found)} {label} symbols misclassified "
        f"(expected {expected.value}): {wrong[:limit]}")


# ---------------------------------------------------------------------------
# Named symbols every category must cover, checked exactly.
# ---------------------------------------------------------------------------

_EXPECTED: List[Tuple[str, str, ComponentType]] = [
    ("Device", "R", ComponentType.RESISTOR),
    ("Device", "R_Small", ComponentType.RESISTOR),
    ("Device", "R_Potentiometer", ComponentType.POTENTIOMETER),
    ("Device", "C", ComponentType.CAPACITOR),
    ("Device", "C_Small", ComponentType.CAPACITOR),
    ("Device", "C_Polarized", ComponentType.CAPACITOR_POLARIZED),
    ("Device", "C_Polarized_Small", ComponentType.CAPACITOR_POLARIZED),
    ("Device", "L", ComponentType.INDUCTOR),
    ("Device", "L_Small", ComponentType.INDUCTOR),
    ("Device", "D", ComponentType.DIODE),
    ("Device", "D_Small", ComponentType.DIODE),
    ("Device", "D_Schottky", ComponentType.SCHOTTKY),
    ("Device", "D_Schottky_Small", ComponentType.SCHOTTKY),
    ("Device", "D_Zener", ComponentType.ZENER),
    ("Device", "D_Zener_Small", ComponentType.ZENER),
    ("Device", "LED", ComponentType.LED),
    ("Device", "LED_Small", ComponentType.LED),
    ("Device", "Q_NPN_BCE", ComponentType.TRANSISTOR_NPN),
    ("Device", "Q_PNP_BCE", ComponentType.TRANSISTOR_PNP),
    ("Device", "Q_NMOS_DGS", ComponentType.NMOS),
    ("Device", "Q_PMOS_DGS", ComponentType.PMOS),
    ("Device", "Q_NJFET_DGS", ComponentType.NJFET),
    ("Device", "Q_PJFET_DGS", ComponentType.PJFET),
    ("Device", "Battery", ComponentType.BATTERY),
    ("Device", "Opamp_Dual", ComponentType.OPAMP),
    # The simulation library is where the source types actually live.
    ("Simulation_SPICE", "VDC", ComponentType.VOLTAGE_SOURCE),
    ("Simulation_SPICE", "IDC", ComponentType.CURRENT_SOURCE),
    ("Simulation_SPICE", "VSIN", ComponentType.AC_SOURCE),
    ("Simulation_SPICE", "OPAMP", ComponentType.OPAMP),
    ("Simulation_SPICE", "NMOS", ComponentType.NMOS),
    ("Simulation_SPICE", "PMOS", ComponentType.PMOS),
    ("Simulation_SPICE", "NJFET", ComponentType.NJFET),
    ("Simulation_SPICE", "PJFET", ComponentType.PJFET),
    ("Simulation_SPICE", "NPN", ComponentType.TRANSISTOR_NPN),
    ("Simulation_SPICE", "PNP", ComponentType.TRANSISTOR_PNP),
    ("Simulation_SPICE", "SWITCH", ComponentType.SWITCH),
    # ESOURCE is a VCVS and GSOURCE a VCCS; both descriptions name
    # *both* quantities, which is exactly what used to trip this up.
    ("Simulation_SPICE", "ESOURCE",
     ComponentType.CONTROLLED_VOLTAGE_SOURCE),
    ("Simulation_SPICE", "GSOURCE",
     ComponentType.CONTROLLED_CURRENT_SOURCE),
    ("Switch", "SW_SPST", ComponentType.SWITCH),
    ("Switch", "SW_DPST", ComponentType.SWITCH),
    ("Switch", "SW_Push", ComponentType.PUSHBUTTON),
]


@pytest.mark.parametrize("lib_name,symbol,expected", _EXPECTED,
                         ids=[f"{l}:{s}" for l, s, _ in _EXPECTED])
def test_fun1_standard_symbol_classified(lib_name: str, symbol: str,
                                         expected: ComponentType) -> None:
    """A named stock KiCad symbol lands in the right component type."""
    symbols = library(lib_name)
    if symbol not in symbols:
        pytest.skip(f"{lib_name}:{symbol} not in this KiCad version")
    lib = symbols[symbol]
    pins = lib.pins_for_unit(1) or lib.pins
    got = classify(f"{lib_name}:{symbol}", lib.reference_prefix, "",
                   len(pins), [p.name for p in pins], lib.hints)
    assert got is expected, (
        f"{lib_name}:{symbol} classified as {got.value}, "
        f"expected {expected.value}")


# ---------------------------------------------------------------------------
# Whole-family sweeps: every symbol KiCad itself describes as X must be X.
# ---------------------------------------------------------------------------

def test_fun1_every_schottky_is_a_schottky() -> None:
    _assert_all(_matching("Diode", r"\bschottky\b"),
                ComponentType.SCHOTTKY, "Schottky")


def test_fun1_every_zener_is_a_zener() -> None:
    _assert_all([(n, c) for n, c in _matching("Diode", r"\bzener\b")
                 if "schottky" not in n.lower()],
                ComponentType.ZENER, "Zener")


def test_fun1_every_bjt_library_symbol_is_a_bjt() -> None:
    """Transistor_BJT holds BJTs - never a FET, never a bare 'component'."""
    allowed = {ComponentType.TRANSISTOR_NPN, ComponentType.TRANSISTOR_PNP,
               ComponentType.UJT}
    wrong = [(n, c.value) for n, c, _ in classified("Transistor_BJT")
             if c not in allowed]
    assert not wrong, f"non-BJTs in Transistor_BJT: {wrong[:8]}"


def test_fun1_every_fet_library_symbol_is_a_fet() -> None:
    """Transistor_FET holds FETs; a BJT type here means a misread."""
    allowed = {ComponentType.NMOS, ComponentType.PMOS,
               ComponentType.NJFET, ComponentType.PJFET}
    wrong = [(n, c.value) for n, c, _ in classified("Transistor_FET")
             if c not in allowed]
    assert not wrong, f"non-FETs in Transistor_FET: {wrong[:8]}"


def test_fun1_p_channel_never_read_as_n_channel() -> None:
    """The distinction that matters most, swept across both FET libraries.

    Complementary dual parts (one n-channel and one p-channel unit in a
    single package, e.g. FDS4559) name both polarities and have no single
    correct part-level answer, so they are left out of the sweep.
    """
    p_types = {ComponentType.PMOS, ComponentType.PJFET}
    fets = {ComponentType.NMOS, ComponentType.PMOS,
            ComponentType.NJFET, ComponentType.PJFET}
    p_rx = re.compile(r"\bp[\s_-]?(channel|mos|jfet)", re.IGNORECASE)
    n_rx = re.compile(r"\bn[\s_-]?(channel|mos|jfet)", re.IGNORECASE)
    for lib_name in ("Transistor_FET", "Device", "Simulation_SPICE"):
        found = [(n, c) for n, c, text in classified(lib_name)
                 if p_rx.search(text) and not n_rx.search(text)
                 and c in fets]
        assert found, f"{lib_name}: probe found no p-channel parts"
        wrong = [(n, c.value) for n, c in found if c not in p_types]
        assert not wrong, (
            f"{lib_name}: p-channel parts read as n-channel: {wrong[:8]}")


def test_fun1_every_operational_amplifier_is_an_opamp() -> None:
    wrong = [(n, c.value) for n, c, _ in classified("Amplifier_Operational")
             if c is not ComponentType.OPAMP]
    assert not wrong, f"non-opamps in Amplifier_Operational: {wrong[:8]}"


def test_fun1_two_pin_meters_are_not_opamps() -> None:
    """A '+'/'-' pair on a two-terminal part is not a differential input."""
    for name in ("Ammeter_DC", "Voltmeter_DC", "Galvanometer"):
        symbols = library("Device")
        if name not in symbols:
            continue
        lib = symbols[name]
        pins = lib.pins_for_unit(1) or lib.pins
        got = classify(f"Device:{name}", lib.reference_prefix, "",
                       len(pins), [p.name for p in pins], lib.hints)
        assert got is not ComponentType.OPAMP, (
            f"Device:{name} ({len(pins)} pins) classified as an op amp")


def test_fun1_switch_library_is_switches() -> None:
    allowed = {ComponentType.SWITCH, ComponentType.PUSHBUTTON,
               ComponentType.CONNECTOR, ComponentType.IC,
               ComponentType.UNKNOWN}
    wrong = [(n, c.value) for n, c, _ in classified("Switch")
             if c not in allowed]
    assert not wrong, f"unexpected types in Switch library: {wrong[:8]}"


def test_fun1_every_classified_type_is_drawable() -> None:
    """Nothing the classifier produces may be undrawable.

    A new ComponentType that no emitter knows about would silently fall
    back to a labelled box, so check the drawing tables cover the ones
    these libraries actually produce.
    """
    from schemaccess import circuitikz

    drawable = (set(circuitikz._BIPOLE_KEYS)
                | set(circuitikz._TRANSISTOR_STYLES)
                | set(circuitikz._GATE_STYLES)
                | {ComponentType.OPAMP, ComponentType.TRANSFORMER,
                   ComponentType.IC, ComponentType.CONNECTOR,
                   ComponentType.UNKNOWN, ComponentType.POWER_FLAG,
                   ComponentType.CRYSTAL, ComponentType.POTENTIOMETER})
    seen = set()
    for lib_name in ("Device", "Diode", "Transistor_BJT", "Transistor_FET",
                     "Simulation_SPICE", "Switch"):
        seen.update(c for _, c, _ in classified(lib_name))
    missing = sorted(c.value for c in seen - drawable)
    assert not missing, f"classified but no drawing rule: {missing}"
