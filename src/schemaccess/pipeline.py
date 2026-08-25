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
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from . import alttext, circuitikz, kicad_parser, netbuilder, renderer
from .model import CircuitGraph, NetKind

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
class ConversionStats:
    """How much of the schematic made it through the conversion.

    ``components`` counts real parts (power symbols such as grounds and
    rails are placed symbols but not components).  ``nodes`` counts nets
    that actually join two or more pins, which is what the alt text calls
    a node.  ``drawn`` excludes components that had no dedicated symbol
    and fell back to a labelled rectangle - they are still connected
    correctly, but they are not really "converted".
    """
    symbols: int = 0
    components: int = 0
    nets: int = 0
    nodes: int = 0
    drawn: int = 0
    described: int = 0
    fallbacks: List[str] = field(default_factory=list)
    undescribed: List[str] = field(default_factory=list)
    #: Which outputs were actually produced, so the report never claims
    #: "0 converted" for a drawing that was never asked for.
    has_drawing: bool = False
    has_text: bool = False
    #: Conversion timings in milliseconds.  These cover the translation
    #: only - reading the schematic, building the graph, writing the
    #: CircuiTikZ and the description.  Running LaTeX is reported
    #: separately, because it is an external tool and dominates the clock.
    parse_ms: float = 0.0
    draw_ms: float = 0.0
    text_ms: float = 0.0
    render_ms: float = 0.0

    @property
    def convert_ms(self) -> float:
        """Total translation time, excluding the external LaTeX render."""
        return self.parse_ms + self.draw_ms + self.text_ms

    def summary_lines(self) -> List[str]:
        """Human-readable report, one item per line."""
        lines = [
            f"{self.components} components in the KiCad schematic "
            f"({self.symbols} symbols placed).",
            f"{self.nodes} nodes ({self.nets} nets in total).",
        ]
        if self.components and self.has_drawing:
            lines.append(f"{self.drawn} of {self.components} components "
                         f"converted to CircuiTikZ symbols.")
        if self.components and self.has_text:
            lines.append(f"{self.described} of {self.components} components "
                         f"described in the alt text.")
        if self.fallbacks:
            lines.append("Drawn as labelled boxes (no dedicated symbol): "
                         + ", ".join(self.fallbacks) + ".")
        if self.undescribed:
            lines.append("Missing from the alt text: "
                         + ", ".join(self.undescribed) + ".")
        if self.convert_ms:
            parts = [f"read {self.parse_ms:.0f} ms"]
            if self.has_drawing:
                parts.append(f"drawing {self.draw_ms:.0f} ms")
            if self.has_text:
                parts.append(f"description {self.text_ms:.0f} ms")
            lines.append(f"Converted in {self.convert_ms:.0f} ms "
                         f"({', '.join(parts)}).")
        if self.render_ms:
            lines.append(f"LaTeX rendering took {self.render_ms:.0f} ms.")
        return lines


@dataclass
class PipelineResult:
    graph: Optional[CircuitGraph] = None
    alt_text: str = ""
    tikz_code: str = ""
    output_files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stats: ConversionStats = field(default_factory=ConversionStats)

    @property
    def ok(self) -> bool:
        return not self.errors


_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9.]){}(?![A-Za-z0-9])")


def summarize(graph: CircuitGraph, tikz_code: str = "", alt_text: str = "",
              fallbacks: Optional[Set[str]] = None) -> ConversionStats:
    """Measure how completely *graph* was converted.

    Safe to call with only the graph: the drawing and description counts
    are simply reported as zero when their output was not generated.
    """
    doc = graph.document
    stats = ConversionStats(
        symbols=len(doc.symbols) if doc is not None else 0,
        components=len(graph.components),
        nets=len(graph.nets),
        nodes=sum(1 for net in graph.nets
                  if len(net.pins) >= 2
                  or (net.kind == NetKind.GROUND and net.pins)),
    )
    boxed = set(fallbacks or ())
    stats.fallbacks = sorted(boxed)
    if tikz_code:
        stats.has_drawing = True
        stats.drawn = len(graph.components) - len(boxed)
    if alt_text:
        stats.has_text = True
        described = [
            ref for ref in graph.components
            if re.search(_REF_TOKEN.pattern.format(re.escape(ref)), alt_text)
        ]
        stats.described = len(described)
        stats.undescribed = sorted(set(graph.components) - set(described))
    return stats


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
    started = time.perf_counter()
    try:
        doc = kicad_parser.parse_file(options.input_path)
    except kicad_parser.KiCadParseError as exc:
        result.errors.append(str(exc))
        return result

    say("Parsing components...")
    say("Generating connectivity graph...")
    graph = netbuilder.build_graph(doc)
    parse_ms = (time.perf_counter() - started) * 1000.0
    result.graph = graph
    result.warnings.extend(graph.warnings)

    fallbacks: Set[str] = set()
    draw_ms = text_ms = render_ms = 0.0
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
        started = time.perf_counter()
        result.alt_text = alttext.generate(graph, options.detail_level)
        text_ms = (time.perf_counter() - started) * 1000.0
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
        started = time.perf_counter()
        result.tikz_code = circuitikz.generate(
            graph, junction_dots=options.junction_dots,
            fallbacks=fallbacks)
        draw_ms = (time.perf_counter() - started) * 1000.0
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
            started = time.perf_counter()
            for fmt in formats:
                say(f"Rendering {fmt.upper()}...")
                try:
                    out = render.render(tex_path, fmt, options.output_dir)
                    result.output_files[fmt] = out
                except renderer.RenderError as exc:
                    result.errors.append(f"{fmt.upper()} rendering failed: {exc}")
            render_ms = (time.perf_counter() - started) * 1000.0

    result.stats = summarize(graph, result.tikz_code, result.alt_text,
                             fallbacks)
    result.stats.parse_ms = parse_ms
    result.stats.draw_ms = draw_ms
    result.stats.text_ms = text_ms
    result.stats.render_ms = render_ms
    for line in result.stats.summary_lines():
        say(line)

    say("Done.")
    return result
