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
    """Translate a 200-component schematic inside the 5-second budget.

    Run with ``-s`` to see the per-stage milliseconds:
        python -m pytest tests/test_performance.py -s
    """
    path = str(fixtures_dir / "big_200.kicad_sch")

    start = time.perf_counter()
    doc = kicad_parser.parse_file(path)
    graph = netbuilder.build_graph(doc)
    read_ms = (time.perf_counter() - start) * 1000.0

    mark = time.perf_counter()
    tikz = circuitikz.generate(graph)
    draw_ms = (time.perf_counter() - mark) * 1000.0

    mark = time.perf_counter()
    alt = alttext.generate(graph, "standard")
    text_ms = (time.perf_counter() - mark) * 1000.0

    elapsed = time.perf_counter() - start

    # Sanity: the work actually happened.
    assert len(graph.components) == 200
    assert tikz and alt

    print(f"\nPER-1: {len(graph.components)} components translated in "
          f"{elapsed * 1000:.1f} ms "
          f"(read {read_ms:.1f} ms, drawing {draw_ms:.1f} ms, "
          f"description {text_ms:.1f} ms); "
          f"budget {_BUDGET_SECONDS * 1000:.0f} ms")

    assert elapsed < _BUDGET_SECONDS, (
        f"full translation took {elapsed * 1000:.0f} ms "
        f"(budget {_BUDGET_SECONDS * 1000:.0f} ms)")
