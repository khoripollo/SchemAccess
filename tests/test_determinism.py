"""REL-1: end-to-end determinism.

Five fully independent runs of parse -> build_graph ->
circuitikz.generate and alttext.generate must produce byte-identical
output for rc_divider, wheatstone and logic_gates.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from conftest import load_graph
from schemaccess import alttext, circuitikz

_REL1_FIXTURES = ("rc_divider.kicad_sch", "wheatstone.kicad_sch",
                  "logic_gates.kicad_sch")
_RUNS = 5


@pytest.mark.parametrize("name", _REL1_FIXTURES)
def test_rel1_five_runs_byte_identical(name: str) -> None:
    """5 repeated full runs yield byte-identical tikz and alt text."""
    outputs: List[Tuple[str, str, str, str]] = []
    for _ in range(_RUNS):
        graph = load_graph(name)
        tikz = circuitikz.generate(graph)
        short = alttext.generate(graph, "short")
        standard = alttext.generate(graph, "standard")
        detailed = alttext.generate(graph, "detailed")
        outputs.append((tikz, short, standard, detailed))
    reference = outputs[0]
    for run, out in enumerate(outputs[1:], start=2):
        assert out == reference, (
            f"{name}: run {run} differs from run 1")
