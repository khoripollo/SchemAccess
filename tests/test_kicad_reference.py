"""Hold SchemAccess's netlists against the ones KiCad exports itself.

Every other test in this suite checks SchemAccess against SchemAccess:
the fixture manifest records what ``netbuilder`` reports, and the netlist
tests re-read the deck against the same graph that produced it.  That
catches regressions but cannot catch a shared misreading -- if the parser
and the net builder agree on a wrong circuit, nothing here notices.

So this module asks KiCad.  ``kicad-cli sch export netlist`` runs on every
fixture in the corpus and writes the netlist *KiCad's* model produces; the
same fixture goes through SchemAccess; and :mod:`schemaccess.netcompare`
compares the two electrically -- components, values, which pins share a
net, which net is ground, and each SPICE device's nodes.  Net *names* are
never compared, because the two tools name the same net differently and
none of that is electrical.

Running these needs KiCad installed: they skip when ``kicad-cli`` cannot
be found, and they carry ``@pytest.mark.slow`` because each export starts
a fresh KiCad process (about half a second apiece, whatever the file
size).  ``python -m pytest tests/test_kicad_reference.py -v`` lists every
fixture with its verdict, and a failure prints the differences.

:data:`KNOWN_DIVERGENCES` records the fixtures where the two tools really
do disagree, with the reason.  Those cases are ``xfail(strict=True)``, so
closing one of the gaps turns the entry into a failure and forces the
table to be updated rather than letting a fix go unnoticed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator, Tuple

import pytest

from conftest import FIXTURES_DIR, VALID_FIXTURES, load_graph
from schemaccess import netcompare, netlist

#: Netlist kind -> the format key :func:`schemaccess.netlist.generate`
#: knows it by.
_GENERATORS: Dict[str, str] = {
    netcompare.KIND_KICAD: "kicad",
    netcompare.KIND_SPICE: "spice",
}

#: (fixture, kind) -> why SchemAccess and KiCad disagree about it.
#:
#: Each entry is a real difference in how the two tools read the file, not
#: a shortcoming of the comparison.  Keep the reason specific enough that
#: whoever closes the gap can tell this entry is the one to delete.
KNOWN_DIVERGENCES: Dict[Tuple[str, str], str] = {
    ("mixed_symbols.kicad_sch", netcompare.KIND_KICAD):
        "SchemAccess joins a wire whose endpoint lands part-way along "
        "another wire even when no junction dot is stored there, while "
        "KiCad needs the junction and leaves the two wires apart.  This "
        "fixture is hand-written and stores only two junctions, so "
        "SchemAccess reads one 31-pin net where KiCad reads about thirty "
        "separate ones.  Schematics Eeschema itself wrote always carry "
        "the junction, which is why every other fixture agrees.",
    ("mixed_symbols.kicad_sch", netcompare.KIND_SPICE):
        "The same missing-junction difference as the .net case, seen as "
        "device terminals landing on one shared node instead of thirty.",
    ("power_symbols.kicad_sch", netcompare.KIND_KICAD):
        "SchemAccess treats every ground symbol as the same ground, so "
        "GND, GNDA, GNDD, GNDREF, Earth and the rest become one net; "
        "KiCad keeps each name as a net of its own.  Unifying them is "
        "what lets a deck have a single SPICE node 0 and the alt text say "
        "'ground', and it is why this fixture's manifest expects 16 nets "
        "rather than KiCad's count.",
    ("power_symbols.kicad_sch", netcompare.KIND_SPICE):
        "The same ground unification: KiCad's twelve separately named "
        "ground nets all map onto SchemAccess's node 0.",
}


def _cases() -> Iterator[pytest.param]:
    """One case per fixture per netlist kind, known gaps marked xfail."""
    for name in VALID_FIXTURES:
        for kind in (netcompare.KIND_KICAD, netcompare.KIND_SPICE):
            reason = KNOWN_DIVERGENCES.get((name, kind))
            marks = ([pytest.mark.xfail(strict=True, reason=reason)]
                     if reason is not None else [])
            yield pytest.param(name, kind, id=f"{name}-{kind}", marks=marks)


@pytest.mark.slow
@pytest.mark.parametrize("name,kind", list(_cases()))
def test_netlist_matches_kicad(name: str, kind: str, kicad_reference) -> None:
    """SchemAccess's netlist describes the circuit KiCad's netlist does."""
    schematic = FIXTURES_DIR / name
    reference = kicad_reference(schematic, kind)
    # The same text the pipeline writes to disk for this format.
    generated = netlist.generate(load_graph(name), _GENERATORS[kind]).text

    report = netcompare.compare(kind, reference, generated,
                                subject=f"{name} ({kind})")
    assert report.ok, "\n" + report.format()


@pytest.mark.slow
def test_comparison_actually_checked_something(kicad_reference) -> None:
    """A comparison that verifies nothing must not count as agreement.

    Every rule in :mod:`schemaccess.netcompare` can only report what it
    managed to compare, so a parser that returned an empty netlist would
    pass every case above in silence.  This pins the floor: on a fixture
    whose wiring both tools agree about, the report has to say that it
    checked components, nets and ground.
    """
    name = "rc_divider.kicad_sch"
    report = netcompare.compare_kicad_netlists(
        kicad_reference(FIXTURES_DIR / name, netcompare.KIND_KICAD),
        netlist.generate(load_graph(name), "kicad").text)

    assert report.ok, "\n" + report.format()
    checks = " ".join(report.checks)
    assert "4 component(s)" in checks
    assert "net(s) joining the same pins" in checks
    assert "ground on" in checks


@pytest.mark.slow
def test_rewiring_a_component_is_caught(kicad_reference) -> None:
    """The comparison fails when the circuit really is wired differently.

    Agreement is only worth something if disagreement is detectable, so
    this swaps one resistor onto the wrong net in SchemAccess's netlist
    and checks that the comparison names that resistor.
    """
    name = "wheatstone.kicad_sch"
    reference = kicad_reference(FIXTURES_DIR / name, netcompare.KIND_KICAD)
    generated = netlist.generate(load_graph(name), "kicad").text

    assert netcompare.compare_kicad_netlists(reference, generated).ok

    # Move R4's pin 2 onto whatever net R1 pin 1 sits on.
    damaged = generated.replace(
        '(node (ref "R4") (pin "2") (pintype "passive"))\n', "", 1)
    damaged = damaged.replace(
        '(node (ref "R1") (pin "1") (pintype "passive"))',
        '(node (ref "R1") (pin "1") (pintype "passive"))\n'
        '      (node (ref "R4") (pin "2") (pintype "passive"))', 1)
    assert damaged != generated, "the netlist did not look as expected"

    report = netcompare.compare_kicad_netlists(reference, damaged)
    assert not report.ok
    assert any("R4 pin 2" in str(diff) for diff in report.differences), \
        report.format()


def test_known_divergences_name_real_fixtures() -> None:
    """Every entry in the table points at a fixture that still exists."""
    for name, kind in KNOWN_DIVERGENCES:
        assert name in VALID_FIXTURES, f"no such fixture: {name}"
        assert kind in _GENERATORS, f"no such netlist kind: {kind}"
        assert (Path(FIXTURES_DIR) / name).is_file()
