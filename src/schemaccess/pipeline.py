"""End-to-end conversion pipeline.

This is the single entry point used by both the GUI and the CLI:

    result = run_pipeline(PipelineOptions(input_path=..., output_dir=...),
                          progress=print)

Stages (reported through the progress callback):

    Reading KiCad schematic...
    Parsing components...
    Generating connectivity graph...
    Creating alt text...
    Generating CircuiTikZ...
    Rendering PDF... / Rendering SVG... / Rendering PNG...
    Done.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import alttext, circuitikz, kicad_parser, netbuilder, renderer
from .model import CircuitGraph

ProgressFn = Callable[[str], None]

DETAIL_LEVELS = ("short", "standard", "detailed")
EXPORT_FORMATS = ("pdf", "svg", "png", "all")


@dataclass
class PipelineOptions:
    input_path: str
    output_dir: str
    generate_alt_text: bool = True
    generate_image: bool = True
    export_format: str = "all"          # pdf | svg | png | all
    detail_level: str = "standard"      # short | standard | detailed
    basename: str = ""                  # default: input file stem
    junction_dots: bool = True          # draw KiCad connection dots


@dataclass
class PipelineResult:
    graph: Optional[CircuitGraph] = None
    alt_text: str = ""
    tikz_code: str = ""
    output_files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_pipeline(options: PipelineOptions,
                 progress: Optional[ProgressFn] = None) -> PipelineResult:
    """Run the full conversion pipeline.  Never raises for input problems;
    check ``result.errors`` instead."""
    say = progress or (lambda _msg: None)
    result = PipelineResult()

    if options.detail_level not in DETAIL_LEVELS:
        result.errors.append(f"Unknown detail level '{options.detail_level}'")
        return result
    if options.export_format not in EXPORT_FORMATS:
        result.errors.append(f"Unknown export format '{options.export_format}'")
        return result

    # ---- Parse -----------------------------------------------------------
    say("Reading KiCad schematic...")
    try:
        doc = kicad_parser.parse_file(options.input_path)
    except kicad_parser.KiCadParseError as exc:
        result.errors.append(str(exc))
        return result

    say("Parsing components...")
    say("Generating connectivity graph...")
    graph = netbuilder.build_graph(doc)
    result.graph = graph
    result.warnings.extend(graph.warnings)

    stem = options.basename or os.path.splitext(
        os.path.basename(options.input_path))[0]
    try:
        os.makedirs(options.output_dir, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"Cannot create output folder: {exc}")
        return result

    # ---- Alt text --------------------------------------------------------
    if options.generate_alt_text:
        say("Creating alt text...")
        result.alt_text = alttext.generate(graph, options.detail_level)
        path = os.path.join(options.output_dir, f"{stem}_alt_text.txt")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(result.alt_text + "\n")
            result.output_files["alt_text"] = path
        except OSError as exc:
            result.errors.append(f"Could not write alt text file: {exc}")

    # ---- CircuiTikZ + rendering -----------------------------------------
    if options.generate_image:
        say("Generating CircuiTikZ...")
        result.tikz_code = circuitikz.generate(
            graph, junction_dots=options.junction_dots)
        tex_path = os.path.join(options.output_dir, f"{stem}.tex")
        try:
            with open(tex_path, "w", encoding="utf-8") as fh:
                fh.write(result.tikz_code)
            result.output_files["tex"] = tex_path
        except OSError as exc:
            result.errors.append(f"Could not write .tex file: {exc}")
            return result

        formats = (["pdf", "svg", "png"] if options.export_format == "all"
                   else [options.export_format])
        render = renderer.Renderer()
        if not render.available():
            result.warnings.append(
                "No LaTeX toolchain found - wrote the .tex file only. "
                + render.install_hint())
        else:
            for fmt in formats:
                say(f"Rendering {fmt.upper()}...")
                try:
                    out = render.render(tex_path, fmt, options.output_dir)
                    result.output_files[fmt] = out
                except renderer.RenderError as exc:
                    result.errors.append(f"{fmt.upper()} rendering failed: {exc}")

    say("Done.")
    return result
