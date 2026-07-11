"""Shared fixtures and helpers for the SchemAccess test suite.

Provides:

* ``FIXTURES_DIR`` / ``fixtures_dir`` - path to ``tests/fixtures``;
* ``MANIFEST`` / ``manifest`` - the fixture corpus manifest (expected
  component/net counts per schematic), loaded once at collection time;
* ``VALID_FIXTURES`` - deterministic list of all valid fixture names,
  usable in ``pytest.mark.parametrize``;
* ``load_graph(name)`` / ``load`` - parse a fixture and build its
  :class:`~schemaccess.model.CircuitGraph` (a fresh graph every call, so
  tests can never interfere through shared mutable state);
* ``LATEX_AVAILABLE`` / ``latex_available`` - whether a LaTeX toolchain
  able to produce PDFs is on ``PATH``;
* an autouse session fixture that regenerates ``big_200.kicad_sch`` with
  ``gen_big.py`` when it is missing;
* registration of the ``slow`` marker.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict

import pytest

from schemaccess import kicad_parser, netbuilder
from schemaccess.model import CircuitGraph
from schemaccess.renderer import Renderer

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

#: True when pdflatex is on PATH (evaluated once, at collection time).
LATEX_AVAILABLE: bool = Renderer().available()


def _read_manifest() -> Dict[str, dict]:
    with open(FIXTURES_DIR / "manifest.json", encoding="utf-8") as fh:
        return json.load(fh)


#: Fixture file name -> expected properties (components, nets, ground...).
MANIFEST: Dict[str, dict] = _read_manifest()

#: All valid fixture names in deterministic (sorted) order.
VALID_FIXTURES = sorted(MANIFEST)


def load_graph(name: str) -> CircuitGraph:
    """Parse fixture *name* and build a fresh circuit graph.

    A new graph is built on every call because downstream generators may
    append to ``graph.warnings``; sharing one instance between tests
    would make results order-dependent.
    """
    doc = kicad_parser.parse_file(str(FIXTURES_DIR / name))
    return netbuilder.build_graph(doc)


def pytest_configure(config: pytest.Config) -> None:
    """Register the custom ``slow`` marker."""
    config.addinivalue_line(
        "markers",
        "slow: slow tests that drive the external LaTeX toolchain")


@pytest.fixture(scope="session", autouse=True)
def ensure_big_200() -> None:
    """Regenerate ``big_200.kicad_sch`` via ``gen_big.py`` if missing."""
    target = FIXTURES_DIR / "big_200.kicad_sch"
    if target.is_file():
        return
    script = FIXTURES_DIR / "gen_big.py"
    subprocess.run([sys.executable, str(script)], check=True,
                   cwd=str(FIXTURES_DIR), timeout=120)
    if not target.is_file():  # pragma: no cover - defensive
        raise RuntimeError("gen_big.py did not produce big_200.kicad_sch")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Absolute path of the fixture corpus directory."""
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def manifest() -> Dict[str, dict]:
    """The parsed ``manifest.json`` describing every valid fixture."""
    return MANIFEST


@pytest.fixture(scope="session")
def latex_available() -> bool:
    """True when pdflatex was found on PATH."""
    return LATEX_AVAILABLE


@pytest.fixture()
def load() -> Callable[[str], CircuitGraph]:
    """Callable fixture: ``load(name) -> CircuitGraph`` (fresh graph)."""
    return load_graph
