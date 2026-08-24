"""Alt-text generator tests.

* I/O-4 - every fixture yields non-empty structured output at every
  detail level.
* FUN-6 - rc_divider description accuracy (standard and detailed).
* REL-3 - two independent runs are identical on every fixture.
"""

from __future__ import annotations

import pytest

from conftest import VALID_FIXTURES, load_graph
from schemaccess import alttext

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
