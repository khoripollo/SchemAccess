"""The TeX files the browser build has to carry.

The web app compiles the CircuiTikZ drawing in the page with pdfTeX
built to WebAssembly.  That engine has no TeX installation behind it: it
fetches every file it needs over HTTP from a "texlive endpoint", and
``web/latex.js`` points that endpoint at ``web/latex/`` so the site works
offline and sends nothing to a third party.  ``web/latex/tex`` is
therefore a flat mirror, and a file the drawing needs that is not in it
is simply a 404 -- the compile stops and the page can only say "LaTeX
did not produce a PDF".

That is how the renderer shipped broken: ``circuitikz.sty`` requires
``xstring``, the preamble asks for ``siunitx`` and ``amssymb``, and none
of the three were mirrored, so no schematic ever rendered in the
browser.  Nothing in the desktop tests could notice, because the desktop
renderer uses a real LaTeX installation.

These tests close that gap from both ends: a cheap one that names the
packages known to be required, and a thorough one that compiles the
fixtures with ``-recorder`` and insists every file LaTeX actually opened
has a counterpart in the mirror.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Set

import pytest

from conftest import LATEX_AVAILABLE
from schemaccess import circuitikz

BUNDLE = Path(__file__).resolve().parent.parent / "web" / "latex" / "tex"

#: Fixtures spanning the symbol kinds a drawing can reach for: passives
#: and a source, a node-shaped part, gates, an AC source, a diode, and
#: the loop arrows with their bend arrow tips.
SPREAD = ["rc_divider.kicad_sch", "opamp_inverting.kicad_sch",
          "logic_gates.kicad_sch", "rlc_series.kicad_sch",
          "led_battery.kicad_sch", "loops_grid.kicad_sch"]

#: Files the engine asks for that are the job's own, or that it probes
#: for and does without.
IGNORED = {"main.tex", "main.aux", "main.log", "main.fls", "main.out",
           "pdflatex.fmt", "texput.log"}


def mirrored() -> Set[str]:
    """Every name the mirror can answer to.

    pdfTeX asks for a font metric by bare name -- the server log shows
    ``GET /latex/tex/cmex7``, not ``cmex7.tfm`` -- and for some plain-TeX
    sources the same way, so the mirror stores those without their
    extension.  Both spellings count as present.
    """
    names = {path.name.lower() for path in BUNDLE.iterdir() if path.is_file()}
    return names | {f"{name}.tfm" for name in names} \
                 | {f"{name}.tex" for name in names}


def test_the_packages_the_preamble_needs_are_mirrored() -> None:
    """Cheap guard, no LaTeX required.

    ``xstring`` is the one worth spelling out: nothing in the preamble
    mentions it, ``circuitikz.sty`` requires it unconditionally, and it
    was the first file to 404.
    """
    for name in ("circuitikz.sty", "tikz.sty", "standalone.cls",
                 "xstring.sty", "xstring.tex", "siunitx.sty",
                 "amssymb.sty", "amsfonts.sty"):
        assert (BUNDLE / name).is_file(), f"web/latex/tex is missing {name}"


def test_the_preamble_has_not_grown_a_package_nobody_mirrored(load) -> None:
    """A new \\usepackage in the generator needs a file in the mirror."""
    body = circuitikz.generate(load("rc_divider.kicad_sch"))
    asked = {line.split("{")[1].rstrip("}")
             for line in body.splitlines()
             if line.startswith(("\\usepackage", "\\documentclass"))
             and "{" in line}
    have = mirrored()
    for package in asked:
        assert f"{package}.sty" in have or f"{package}.cls" in have, (
            f"the preamble loads '{package}' but web/latex/tex has no "
            f"{package}.sty or .cls")


@pytest.mark.slow
@pytest.mark.skipif(not LATEX_AVAILABLE,
                    reason="pdflatex is not available on PATH")
@pytest.mark.parametrize("name", SPREAD)
def test_every_file_a_drawing_opens_is_mirrored(name: str, load) -> None:
    """Compile for real and insist the mirror could have served it all.

    ``-recorder`` makes pdfTeX write a .fls naming every file it opened,
    which is exactly the set the browser would have to fetch.
    """
    have = mirrored()
    graph = load(name)
    for loops in (False, True):
        tex = circuitikz.generate(graph, loops=loops)
        with tempfile.TemporaryDirectory() as work:
            Path(work, "main.tex").write_text(tex, encoding="utf-8")
            subprocess.run(
                ["pdflatex", "-recorder", "-interaction=nonstopmode",
                 "-halt-on-error", "main.tex"],
                cwd=work, capture_output=True, timeout=300)
            fls = Path(work, "main.fls")
            assert fls.is_file(), f"{name}: pdflatex wrote no .fls"

            opened = set()
            for line in fls.read_text(encoding="utf-8",
                                      errors="replace").splitlines():
                if not line.startswith("INPUT "):
                    continue
                full = line[6:].strip()
                if full.lower().startswith(work.lower()):
                    continue
                base = os.path.basename(full).lower()
                # A .pk is a bitmap the local install generated for want
                # of an outline; the browser never asks for one.
                if base in IGNORED or base.endswith(".pk"):
                    continue
                opened.add(base)

            absent = sorted(n for n in opened if n not in have)
            assert not absent, (
                f"{name} (loops={loops}) needs files the browser bundle "
                f"cannot serve: {', '.join(absent)}. Copy them into "
                f"web/latex/tex (metrics without the .tfm extension).")
