"""Alt-text generator tests.

* I/O-4 - every fixture yields non-empty structured output at every
  detail level.
* FUN-6 - rc_divider description accuracy (standard and detailed).
* REL-3 - two independent runs are identical on every fixture.
"""

from __future__ import annotations

import pytest

from conftest import VALID_FIXTURES, load_graph
from schemaccess import alttext, analyzer
from schemaccess.model import NetKind
from schemaccess.netbuilder import node_names

DETAIL_LEVELS = ("short", "standard", "detailed")


@pytest.mark.parametrize("level", DETAIL_LEVELS)
@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_io4_alt_text_nonempty_structured(name: str, level: str,
                                          load) -> None:
    """Alt text is non-empty structured prose at every detail level."""
    text = alttext.generate(load(name), level)
    assert text.strip(), f"{name}/{level}: empty alt text"
    lines = text.splitlines()
    # Structured output: a counts sentence first, no trailing whitespace.
    assert lines[0].startswith("There "), lines[0]
    assert "element" in lines[0] and "node" in lines[0]
    for line in lines:
        assert line == line.rstrip()


def test_fun6_rc_divider_standard_description_accuracy(load) -> None:
    """The standard rc_divider description states counts, topology,
    polarity, all four refs, both resistor values and ground."""
    text = alttext.generate(load("rc_divider.kicad_sch"), "standard")
    assert "There are 4 elements and 3 nodes" in text
    assert "in parallel with" in text
    assert "in series with" in text
    assert "positive terminal" in text
    for ref in ("R1", "R2", "C1", "V1"):
        assert ref in text, f"missing component reference {ref}"
    assert "100 Ohm" in text
    assert "20 Ohm" in text
    assert "ground" in text


def test_fun6_rc_divider_detailed_mentions_structures(load) -> None:
    """The detailed level names the divider and filter structures."""
    text = alttext.generate(load("rc_divider.kicad_sch"), "detailed")
    assert "voltage divider" in text
    assert "low-pass filter" in text
    # Detailed output also lists per-component connections.
    assert "Connections by component:" in text


@pytest.mark.parametrize("level", DETAIL_LEVELS)
@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_rel3_alt_text_determinism(name: str, level: str) -> None:
    """Two fully independent runs produce byte-identical alt text."""
    first = alttext.generate(load_graph(name), level)
    second = alttext.generate(load_graph(name), level)
    assert first == second


def test_fun6_polarity_dot_reported_in_detailed_alt_text():
    """A dot is visual information a sighted reader gets for free, so the
    detailed description has to state it."""
    from conftest import load_graph
    from schemaccess import alttext

    graph = load_graph("mixed_symbols.kicad_sch")
    dotted = sorted(ref for ref, c in graph.components.items() if c.dots)
    assert dotted, "fixture no longer has a dotted symbol"

    text = alttext.generate(graph, "detailed")
    for ref in dotted:
        assert f"labelled {ref} is marked with a" in text, (
            f"{ref}'s polarity dot is not described")
    # Short and standard stay uncluttered.
    assert "dot" not in alttext.generate(graph, "short")


def test_fun6_polar_value_described_in_words():
    """A phasor is spoken as magnitude plus phase, with the angle after the
    reference so the sentence stays readable."""
    from schemaccess.alttext import format_value
    from schemaccess.model import ComponentType

    ac = ComponentType.AC_SOURCE
    assert format_value("10\u222090", ac) == (
        "10 Volt at an angle of 90 degrees")
    assert format_value("10<90", ac) == "10 Volt at an angle of 90 degrees"
    assert format_value("10@90", ac) == "10 Volt at an angle of 90 degrees"
    assert format_value("1.5<-90deg", ac) == (
        "1.5 Volt at an angle of -90 degrees")
    assert format_value("10<1", ac) == "10 Volt at an angle of 1 degree"
    # Non-polar values are untouched.
    assert format_value("5V", ac) == "5 Volt"


def test_an_opamp_without_feedback_is_not_a_warning(load) -> None:
    """Open-loop is a topology, not a fault.

    These notes are read out under "Warnings", so one that ends "which is
    the usual arrangement for a comparator" is announcing that nothing is
    wrong -- and it guesses: an op amp with no feedback is equally the
    spare half of a dual part that was placed and left alone.  Nothing is
    lost by silence, because where each pin goes is still stated three
    times over.
    """
    for name in ("opamp_partnumber.kicad_sch", "mixed_symbols.kicad_sch"):
        graph = load(name)
        notes = analyzer.analyze(graph).notes
        assert not any("feedback path" in note or "open-loop" in note
                       for note in notes), f"{name}: {notes}"

        text = alttext.generate(graph, "detailed")
        assert "open-loop" not in text and "comparator" not in text
        # The connections the note sat next to are untouched.
        assert "operational amplifier with" in text
        assert "U1: pin" in text

    # Only that one note went: an op amp whose feedback the analyzer can
    # see but cannot name is a genuine "could not work this out".
    notes = analyzer.analyze(load("mixed_symbols.kicad_sch")).notes
    assert any("configuration was not recognized" in note for note in notes)


def test_fun6_polar_source_reads_naturally():
    """The angle follows the reference designator, not the magnitude."""
    from conftest import load_graph
    from schemaccess import alttext

    text = alttext.generate(load_graph("mixed_symbols.kicad_sch"), "standard")
    assert "10 Volt AC voltage source labelled V1 at an angle of 90 degrees" \
        in text, text


# ---------------------------------------------------------------------------
# Connections by node
# ---------------------------------------------------------------------------
#
# The per-component listing says what each part is wired to; this one says
# what meets at each junction.  The two have to agree, and the node view
# has to name every node the counts sentence promised.

_HEADING = "Connections by node:"


def is_node_net(net) -> bool:
    """A net the circuit treats as a node, stated independently here."""
    return len(net.pins) >= 2 or (net.kind == NetKind.GROUND and net.pins)


def _node_section(graph, text: str) -> list:
    """The lines under the node heading.

    The section is followed by plain sentences rather than another
    heading, so it is bounded by the node names themselves: the first
    line that does not open with one ends it.  That makes a missing node
    line show up as a short section rather than being papered over.
    """
    lines = text.splitlines()
    if _HEADING not in lines:
        return []
    names = node_names(graph)
    openers = tuple(sorted(
        {names[net.net_id][:1].upper() + names[net.net_id][1:] + " "
         for net in graph.nets if is_node_net(net)}))
    out = []
    for line in lines[lines.index(_HEADING) + 1:]:
        if not line.startswith(openers):
            break
        out.append(line)
    return out


def test_node_connections_only_in_detailed(load) -> None:
    """The node listing is exhaustive detail, so it stays out of the
    shorter levels."""
    graph = load("wheatstone.kicad_sch")
    assert "Connections by node:" in alttext.generate(graph, "detailed")
    assert "Connections by node:" not in alttext.generate(graph, "standard")
    assert "Connections by node:" not in alttext.generate(graph, "short")


def test_node_connections_read_as_sentences(load) -> None:
    """Each line names a node and the pins that meet there."""
    graph = load("wheatstone.kicad_sch")
    text = alttext.generate(graph, "detailed")
    lines = _node_section(graph, text)

    assert "Ground joins V1 pin 2 (-), R2 pin 2, and R4 pin 2." in lines
    assert "Node 1 (A) joins R1 pin 2 and R2 pin 1." in lines
    assert "Node 3 joins V1 pin 1 (+), R1 pin 1, and R3 pin 1." in lines
    # Ground comes first, as it does everywhere else in the description.
    assert lines[0].startswith("Ground ")


def test_node_connections_name_terminals_by_role(load) -> None:
    """A named terminal reads as itself, not as a pin number.

    This is the whole reason for the node view: 'the inverting input of
    U1' tells a listener what meets at that junction, where 'U1 pin 1'
    makes them go and look it up.
    """
    graph = load("opamp_inverting.kicad_sch")
    lines = _node_section(graph, alttext.generate(graph, "detailed"))
    joined = " ".join(lines)
    assert "the inverting input of U1" in joined
    assert "the output of U1" in joined
    # A ground net is a node on the strength of its ground symbol, so it
    # can reach a single pin; that must not read as "joins".
    assert "Ground is connected to the non-inverting input of U1." in lines


def test_node_connections_name_transistor_terminals(load) -> None:
    graph = load("mixed_symbols.kicad_sch")
    joined = " ".join(_node_section(graph,
                                    alttext.generate(graph, "detailed")))
    assert "the emitter of Q2" in joined
    assert "the base of Q2" in joined


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_node_listing_covers_every_node(name: str, load) -> None:
    """One line per node, and as many as the counts sentence announced."""
    graph = load(name)
    text = alttext.generate(graph, "detailed")
    expected = sum(1 for net in graph.nets if is_node_net(net))

    lines = _node_section(graph, text)
    assert len(lines) == expected, \
        f"{name}: {len(lines)} node lines for {expected} nodes"
    assert f" and {expected} node" in text.splitlines()[0]


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_node_listing_names_every_pin_on_a_node(name: str, load) -> None:
    """Nothing is lost reading the wiring the other way round.

    Every pin the graph puts on a node has to appear in that node's own
    line, so the node view can be trusted on its own rather than being a
    lossy summary of the component view.
    """
    graph = load(name)
    lines = _node_section(graph, alttext.generate(graph, "detailed"))
    nodes = [net for net in graph.nets if is_node_net(net)]
    assert len(lines) == len(nodes)

    for net, line in zip(nodes, lines):
        for ref, number in net.pins:
            comp = graph.components.get(ref)
            if comp is None or number not in comp.pins:
                continue
            assert ref in line, f"{name}: {ref} missing from {line!r}"
