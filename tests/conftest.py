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
* ``KICAD_CLI`` / ``kicad_reference`` - KiCad's own command-line tool and a
  callable that exports a reference netlist with it, for the tests that
  check SchemAccess's netlists against KiCad's;
* ``LCAPY_AVAILABLE`` - whether Lcapy is installed, for the tests that
  load an exported Lcapy netlist with ``lcapy.Circuit``;
* an autouse session fixture that regenerates ``big_200.kicad_sch`` with
  ``gen_big.py`` when it is missing;
* registration of the ``slow`` marker.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Optional

import pytest

from schemaccess import kicad_parser, netbuilder
from schemaccess.model import CircuitGraph
from schemaccess.renderer import Renderer

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

#: True when pdflatex is on PATH (evaluated once, at collection time).
LATEX_AVAILABLE: bool = Renderer().available()

#: ``--format`` value kicad-cli wants for each netlist kind SchemAccess
#: writes, keyed by the extension SchemAccess gives that kind.
KICAD_NETLIST_FORMATS: Dict[str, str] = {
    "net": "kicadsexpr",
    "cir": "spice",
}

# Where KiCad puts its command-line tool when it is not on PATH.  The
# globs are sorted in reverse so the newest installed version wins.
_KICAD_CLI_GLOBS = (
    r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
    r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/lib/kicad/bin/kicad-cli",
    "/snap/bin/kicad.kicad-cli",
)


def _find_kicad_cli() -> Optional[str]:
    """Locate ``kicad-cli``, or return None when KiCad is not installed.

    ``SCHEMACCESS_KICAD_CLI`` overrides the search, so a machine with
    several KiCad versions can say which one is the reference.
    """
    override = os.environ.get("SCHEMACCESS_KICAD_CLI", "").strip()
    if override:
        return override if os.path.isfile(override) else None
    found = shutil.which("kicad-cli")
    if found:
        return found
    for pattern in _KICAD_CLI_GLOBS:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    return None


#: KiCad's command-line tool, or None (evaluated once, at collection time).
KICAD_CLI: Optional[str] = _find_kicad_cli()

#: True when Lcapy can be imported, so an exported Lcapy netlist can be
#: handed to ``lcapy.Circuit`` itself.  Only looked for, never imported
#: here: Lcapy brings SymPy with it and takes seconds to load, which every
#: test run would pay whether or not it ran an Lcapy test.
LCAPY_AVAILABLE: bool = importlib.util.find_spec("lcapy") is not None


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


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ``--schematic PATH`` for auditing a schematic of your own."""
    parser.addoption(
        "--schematic", action="append", default=[], metavar="PATH",
        help="an extra .kicad_sch file to audit alongside the fixture "
             "corpus; repeatable.  Needs no manifest entry - the audit "
             "derives its expectations from the file itself.")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize any test taking ``schematic`` over every input file.

    That is the fixture corpus plus whatever ``--schematic`` supplies,
    so pointing the audit at your own design needs no code change.
    """
    if "schematic" not in metafunc.fixturenames:
        return
    paths = [FIXTURES_DIR / name for name in VALID_FIXTURES]
    for given in metafunc.config.getoption("--schematic"):
        path = Path(given).expanduser()
        if not path.is_file():
            raise pytest.UsageError(f"--schematic: no such file: {path}")
        paths.append(path.resolve())
    metafunc.parametrize("schematic", paths, ids=[p.name for p in paths])


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


@pytest.fixture(scope="session")
def kicad_reference(tmp_path_factory) -> Callable[[Path, str], str]:
    """Callable fixture: ``kicad_reference(schematic, kind) -> text``.

    Runs ``kicad-cli sch export netlist`` on *schematic* and returns what
    KiCad wrote, so a test can hold SchemAccess's own netlist against it.
    *kind* is ``'net'`` for KiCad's S-expression netlist or ``'cir'`` for
    a SPICE deck.

    Each (schematic, kind) pair is exported once per session and cached:
    starting kicad-cli costs about half a second whatever the file size,
    which is most of the runtime of the whole comparison suite.
    """
    if KICAD_CLI is None:
        pytest.skip("kicad-cli not found; set SCHEMACCESS_KICAD_CLI to "
                    "point at it")
    out_dir = tmp_path_factory.mktemp("kicad_reference")
    cache: Dict[tuple, str] = {}

    def export(schematic: Path, kind: str) -> str:
        key = (str(schematic), kind)
        if key in cache:
            return cache[key]
        target = out_dir / f"{schematic.stem}.{kind}"
        completed = subprocess.run(
            [KICAD_CLI, "sch", "export", "netlist",
             "--format", KICAD_NETLIST_FORMATS[kind],
             "-o", str(target), str(schematic)],
            capture_output=True, text=True, timeout=300)
        if completed.returncode != 0 or not target.is_file():
            pytest.fail(
                f"kicad-cli could not export a {kind} netlist for "
                f"{schematic.name} (exit {completed.returncode}):\n"
                f"{completed.stdout}{completed.stderr}")
        cache[key] = target.read_text(encoding="utf-8-sig", errors="replace")
        return cache[key]

    return export
