"""PER-1: performance.

A full translation of the generated 200-component stress schematic
(parse + graph + CircuiTikZ + standard alt text) completes within the
5-second budget (generous CI margin).
"""

from __future__ import annotations

import time

from schemaccess import alttext, circuitikz, kicad_parser, netbuilder

_BUDGET_SECONDS = 5.0


def test_per1_big_200_full_translation_under_budget(fixtures_dir) -> None:
    path = str(fixtures_dir / "big_200.kicad_sch")
    start = time.perf_counter()
    doc = kicad_parser.parse_file(path)
    graph = netbuilder.build_graph(doc)
    tikz = circuitikz.generate(graph)
    alt = alttext.generate(graph, "standard")
    elapsed = time.perf_counter() - start

    # Sanity: the work actually happened.
    assert len(graph.components) == 200
    assert tikz and alt

    assert elapsed < _BUDGET_SECONDS, (
        f"full translation took {elapsed:.2f}s "
        f"(budget {_BUDGET_SECONDS:.1f}s)")
