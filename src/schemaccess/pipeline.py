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
from typing import Callable, Dict, List, Optional, Sequence, Set

from . import (alttext, circuitikz, kicad_parser, netbuilder, netlist,
               pdfwriter, power, renderer, svgpreview, units)
from .loops import find_meshes
from .model import CircuitGraph, NetKind

ProgressFn = Callable[[str], None]

DETAIL_LEVELS = ("short", "standard", "detailed")
EXPORT_FORMATS = ("pdf", "svg", "png", "all")

#: Netlist formats :func:`run_pipeline` can write, plus the "all" alias.
NETLIST_FORMATS = netlist.FORMATS + ("all",)


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
    #: Netlist formats to write; empty writes none.  Accepts the keys of
    #: :data:`schemaccess.netlist.FORMATS`, or ``("all",)``.
    netlist_formats: Sequence[str] = ()
    #: Write the dependency-free SVG drawing (``<stem>_preview.svg``).
    #: Unlike the rendered images this needs no LaTeX toolchain, so it is
    #: the only picture available on a machine without one.
    svg_preview: bool = False
    #: Write the same drawing as a vector PDF (``<stem>_preview.pdf``).
    #: The name keeps it clear of ``<stem>.pdf``, which is the LaTeX
    #: rendering when a toolchain is present.
    pdf_preview: bool = False
    #: Convert in memory and report, writing nothing.  Used by the CLI's
    #: --check mode to answer "what would convert?" without producing
    #: files or needing a LaTeX toolchain.
    dry_run: bool = False
    show_loops: bool = False
    show_units: bool = False


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
    grounds: List[str] = field(default_factory=list)
    supplies: List[str] = field(default_factory=list)
    loops: List[str] = field(default_factory=list)
    loops_reason: str = ""
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
        if self.grounds or self.supplies:
            parts = [f"{kind} {', '.join(names)}" for kind, names in
                     (("ground", self.grounds), ("supply", self.supplies))
                     if names]
            lines.append(f"Power symbols: {'; '.join(parts)}.")
        if self.loops:
            lines.append(f"Loop currents: {', '.join(self.loops)}.")
        elif self.loops_reason:
            lines.append(f"Loop currents not drawn: {self.loops_reason}.")
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
    #: Netlist format -> generated text, for the formats that were asked
    #: for.  Notes about anything a format could not express are appended
    #: to :attr:`warnings`, never dropped.
    netlists: Dict[str, str] = field(default_factory=dict)
    svg: str = ""
    pdf: bytes = b""
    output_files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stats: ConversionStats = field(default_factory=ConversionStats)

    @property
    def ok(self) -> bool:
        return not self.errors


_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9.]){}(?![A-Za-z0-9])")


def summarize(graph: CircuitGraph, tikz_code: str = "", alt_text: str = "",
              fallbacks: Optional[Set[str]] = None,
              loops: bool = False) -> ConversionStats:
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
    for inst in (doc.symbols if doc is not None else ()):
        lib = doc.lib_symbol_for(inst)
        if lib is None or not (lib.is_power or inst.reference.startswith("#")) \
                or inst.reference.startswith("#FLG"):
            continue
        names = stats.grounds if power.is_ground(inst, lib) else stats.supplies
        if power.net_name(inst) not in names:
            names.append(power.net_name(inst))
    stats.grounds.sort()
    stats.supplies.sort()
    if loops:
        found = find_meshes(graph)
        stats.loops = ([f"{mesh.name} {mesh.sense}" for mesh in found.meshes]
                       if found.ok else [])
        stats.loops_reason = "" if found.ok else found.reason
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


def _draw_without_latex(result: "PipelineResult", graph: CircuitGraph,
                        options: PipelineOptions, stem: str,
                        formats: List[str]) -> List[str]:
    """Draw the requested vector formats without a LaTeX toolchain.

    Returns the formats actually written.  PNG is not among them: it
    would need a rasteriser, and a missing file the caller is told about
    beats a silently wrong one.
    """
    made: List[str] = []
    for fmt in formats:
        if fmt == "svg":
            result.svg = svgpreview.generate(
                graph, junction_dots=options.junction_dots,
                loops=options.show_loops)
            payload, mode = result.svg, "w"
        elif fmt == "pdf":
            result.pdf = pdfwriter.generate(
                graph, junction_dots=options.junction_dots,
                loops=options.show_loops)
            payload, mode = result.pdf, "wb"
        else:
            continue
        if options.dry_run:
            made.append(fmt)
            continue
        path = os.path.join(options.output_dir, f"{stem}.{fmt}")
        try:
            if mode == "wb":
                with open(path, "wb") as fh:
                    fh.write(payload)
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(payload)
            result.output_files[fmt] = path
            made.append(fmt)
        except OSError as exc:
            result.errors.append(f"Could not write the {fmt.upper()}: {exc}")
    return made


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
    for fmt in options.netlist_formats:
        if fmt not in NETLIST_FORMATS:
            result.errors.append(f"Unknown netlist format '{fmt}'")
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
    drawn = units.apply(graph) if options.show_units else graph

    fallbacks: Set[str] = set()
    draw_ms = text_ms = render_ms = 0.0
    stem = options.basename or os.path.splitext(
        os.path.basename(options.input_path))[0]
    if not options.dry_run:
        try:
            os.makedirs(options.output_dir, exist_ok=True)
        except OSError as exc:
            result.errors.append(f"Cannot create output folder: {exc}")
            return result

    # ---- Alt text --------------------------------------------------------
    if options.generate_alt_text:
        say("Creating alt text...")
        started = time.perf_counter()
        result.alt_text = alttext.generate(graph, options.detail_level,
                                           loops=options.show_loops)
        text_ms = (time.perf_counter() - started) * 1000.0
        if not options.dry_run:
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
            drawn, junction_dots=options.junction_dots,
            fallbacks=fallbacks, loops=options.show_loops)
        draw_ms = (time.perf_counter() - started) * 1000.0
        if options.dry_run:
            formats = []            # nothing written, nothing to render
        else:
            tex_path = os.path.join(options.output_dir, f"{stem}.tex")
            try:
                with open(tex_path, "w", encoding="utf-8") as fh:
                    fh.write(result.tikz_code)
                result.output_files["tex"] = tex_path
            except OSError as exc:
                result.errors.append(f"Could not write .tex file: {exc}")
                return result

            formats = (["pdf", "svg", "png"]
                       if options.export_format == "all"
                       else [options.export_format])

        if formats:
            render = renderer.Renderer()
            if not render.available():
                # No LaTeX here, but the drawing does not depend on it:
                # svgpreview and pdfwriter work straight off the graph.
                # Produce what they can rather than leaving the reader
                # with nothing, and say plainly what they are getting.
                say("No LaTeX toolchain - drawing directly...")
                made = _draw_without_latex(result, drawn, options, stem,
                                           formats)
                if made:
                    result.warnings.append(
                        "No LaTeX toolchain found, so the "
                        + ", ".join(sorted(made)).upper()
                        + " was drawn directly instead of through "
                        "circuitikz. It is the same circuit, drawn by "
                        "SchemAccess rather than by LaTeX; compile the "
                        ".tex for the circuitikz rendering. "
                        + render.install_hint())
                if "png" in formats:
                    result.warnings.append(
                        "PNG needs a LaTeX toolchain and an image "
                        "converter; the PDF and SVG above are vector and "
                        "scale without loss.")
            else:
                started = time.perf_counter()
                for fmt in formats:
                    say(f"Rendering {fmt.upper()}...")
                    try:
                        out = render.render(tex_path, fmt,
                                            options.output_dir)
                        result.output_files[fmt] = out
                    except renderer.RenderError as exc:
                        result.errors.append(
                            f"{fmt.upper()} rendering failed: {exc}")
                render_ms = (time.perf_counter() - started) * 1000.0

    # ---- Netlists --------------------------------------------------------
    if options.netlist_formats:
        say("Generating netlists...")
        wanted = (list(netlist.FORMATS) if "all" in options.netlist_formats
                  else [f for f in netlist.FORMATS
                        if f in options.netlist_formats])
        for fmt in wanted:
            generated = netlist.generate(graph, fmt)
            result.netlists[fmt] = generated.text
            # A netlist that cannot express a component says so; that is a
            # fact about the circuit, so it belongs with the warnings.
            for note in generated.notes:
                if note not in result.warnings:
                    result.warnings.append(note)
            if options.dry_run:
                continue
            path = os.path.join(options.output_dir,
                                stem + netlist.EXTENSIONS[fmt])
            try:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(generated.text)
                result.output_files[f"netlist_{fmt}"] = path
            except OSError as exc:
                result.errors.append(
                    f"Could not write the {fmt} netlist: {exc}")

    # ---- SVG preview -----------------------------------------------------
    if options.svg_preview:
        say("Drawing SVG preview...")
        result.svg = svgpreview.generate(
            drawn, junction_dots=options.junction_dots,
            loops=options.show_loops)
        if not options.dry_run:
            path = os.path.join(options.output_dir, f"{stem}_preview.svg")
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(result.svg)
                result.output_files["svg_preview"] = path
            except OSError as exc:
                result.errors.append(f"Could not write the SVG preview: {exc}")

    # ---- PDF drawing -----------------------------------------------------
    if options.pdf_preview:
        say("Drawing PDF...")
        result.pdf = pdfwriter.generate(
            drawn, junction_dots=options.junction_dots,
            loops=options.show_loops)
        if not options.dry_run:
            path = os.path.join(options.output_dir, f"{stem}_preview.pdf")
            try:
                with open(path, "wb") as fh:
                    fh.write(result.pdf)
                result.output_files["pdf_preview"] = path
            except OSError as exc:
                result.errors.append(f"Could not write the PDF: {exc}")

    result.stats = summarize(graph, result.tikz_code, result.alt_text,
                             fallbacks, loops=options.show_loops)
    if result.stats.loops_reason:
        result.warnings.append(
            f"Loop currents are not drawn: {result.stats.loops_reason}.")
    result.stats.parse_ms = parse_ms
    result.stats.draw_ms = draw_ms
    result.stats.text_ms = text_ms
    result.stats.render_ms = render_ms
    for line in result.stats.summary_lines():
        say(line)

    say("Done.")
    return result
