"""Command-line interface for SchemAccess.

Installed as the ``schemaccess`` console script (see ``pyproject.toml``)::

    schemaccess my_circuit.kicad_sch -o out --format svg --print-alt

The CLI is a thin wrapper around :func:`schemaccess.pipeline.run_pipeline`:
it parses arguments into :class:`~schemaccess.pipeline.PipelineOptions`,
streams progress lines to stdout, and reports warnings/errors on stderr.

Exit codes:

* ``0`` - success,
* ``1`` - the conversion ran but produced errors,
* ``2`` - bad command-line arguments (argparse).
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, circuitikz, netcompare, netlist, pipeline

#: Preferred, deterministic ordering for the "wrote:" report lines.
_FILE_ORDER = ("alt_text", "tex", "pdf", "svg", "png",
               "pdf_preview", "svg_preview",
               "netlist_spice", "netlist_kicad", "netlist_text",
               "netlist_csv")

_EPILOG = """\
notes:
  If both --no-alt-text and --no-image are given, no output files are
  produced: the schematic is still parsed and its connectivity checked
  (useful as a quick validation pass), any warnings are reported, and
  the exit code is 0 when the file is readable.

  --compare-net and --compare-cir check a conversion against KiCad's own
  netlist for the same schematic.  Export the reference from KiCad first
  (Tools > Generate Netlist, or 'kicad-cli sch export netlist'), then
  point these at the files.  Like --check they write nothing, print the
  verdict and any differences, and exit 1 when the two disagree.  Net
  names are not compared: a net matches when it joins the same component
  pins, whatever either tool calls it.

  By default the schematic is converted again, in memory, to produce the
  netlist to compare.  To compare a netlist already on disk instead --
  one written by an earlier run, by the web front end, or by another
  machine -- give it with --generated-net / --generated-cir.  When every
  comparison has both of its files, nothing needs converting and the
  schematic may be left off the command line altogether.

examples:
  schemaccess board.kicad_sch
  schemaccess board.kicad_sch -o out --format svg --detail detailed
  schemaccess board.kicad_sch --no-image --print-alt --quiet
  schemaccess board.kicad_sch --no-junction-dots --format pdf
  schemaccess board.kicad_sch --loops --detail detailed
  schemaccess board.kicad_sch --netlist all --svg-preview --pdf-preview
  schemaccess board.kicad_sch --netlist spice,csv --no-image
  schemaccess board.kicad_sch --compare-net kicad/board.net
  schemaccess board.kicad_sch --compare-net board.net --compare-cir board.cir
  schemaccess --compare-net kicad/board.net --generated-net out/board.net
"""

#: Which netlist format each comparison needs SchemAccess to generate.
_COMPARE_FORMATS = {
    netcompare.KIND_KICAD: "kicad",
    netcompare.KIND_SPICE: "spice",
}


def _existing_file(value: str) -> str:
    """argparse type: the input must be an existing file."""
    if not os.path.isfile(value):
        raise argparse.ArgumentTypeError(f"input file not found: {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the ``schemaccess`` command."""
    parser = argparse.ArgumentParser(
        prog="schemaccess",
        description=(
            "Convert a KiCad .kicad_sch schematic into screen-reader alt "
            "text and a CircuiTikZ/LaTeX rendering (PDF/SVG/PNG)."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=_existing_file,
        default=None,
        help="path to the KiCad schematic (.kicad_sch) to convert.  "
             "Optional only when every comparison is given both of its "
             "files, which needs no conversion",
    )
    parser.add_argument(
        "-o", "--output-dir",
        metavar="DIR",
        default=None,
        help="folder for generated files "
             "(default: '<input folder>/accessible')",
    )
    parser.add_argument(
        "--no-alt-text",
        action="store_true",
        help="skip generating the natural-language alt text",
    )
    parser.add_argument(
        "--no-image",
        action="store_true",
        help="skip generating CircuiTikZ/LaTeX and rendered images",
    )
    parser.add_argument(
        "-f", "--format",
        choices=pipeline.EXPORT_FORMATS,
        default="all",
        help="image format(s) to render (default: %(default)s)",
    )
    parser.add_argument(
        "-d", "--detail",
        choices=pipeline.DETAIL_LEVELS,
        default="standard",
        help="alt-text detail level (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what this schematic converts to without writing any "
             "files; exits 1 if a component did not convert or was left "
             "out of the description",
    )
    parser.add_argument(
        "--no-junction-dots",
        action="store_true",
        help="omit the connection dots drawn where wires meet "
             "(they are included by default, as KiCad draws them)",
    )
    parser.add_argument(
        "--switch-style",
        choices=sorted(circuitikz.SWITCH_STYLE_KEYS),
        default="default",
        help="how to draw switches: 'default' is a plain open contact, "
             "'closing' and 'opening' add the arrow showing the contact "
             "in motion (default: default)",
    )
    parser.add_argument(
        "--loops",
        action="store_true",
        help="for teaching mesh analysis: draw each loop current (I1, I2, "
             "...) as an arrow in its window, turning the way its current "
             "flows, and describe the loops in the alt text.  Works on flat "
             "circuits of two-terminal parts, however many loops they "
             "have; any other circuit is converted without them and a "
             "warning says why",
    )
    parser.add_argument(
        "--netlist",
        metavar="FORMATS",
        default="",
        help="also write netlists: a comma-separated list of "
             + ", ".join(netlist.FORMATS)
             + ", or 'all' (default: none)",
    )
    parser.add_argument(
        "--compare-net",
        metavar="FILE",
        type=_existing_file,
        default=None,
        help="validate against KiCad: compare the KiCad-format netlist "
             "SchemAccess generates for this schematic against a .net file "
             "exported from KiCad, and report PASS or FAIL with any "
             "differences (writes no files)",
    )
    parser.add_argument(
        "--compare-cir",
        metavar="FILE",
        type=_existing_file,
        default=None,
        help="the same check against a SPICE deck exported from KiCad; may "
             "be given together with --compare-net",
    )
    parser.add_argument(
        "--generated-net",
        metavar="FILE",
        type=_existing_file,
        default=None,
        help="compare this already-written .net file against --compare-net "
             "instead of converting the schematic again",
    )
    parser.add_argument(
        "--generated-cir",
        metavar="FILE",
        type=_existing_file,
        default=None,
        help="compare this already-written .cir file against --compare-cir "
             "instead of converting the schematic again",
    )
    parser.add_argument(
        "--svg-preview",
        action="store_true",
        help="also write <stem>_preview.svg, drawn without LaTeX (useful "
             "on a machine with no TeX toolchain)",
    )
    parser.add_argument(
        "--pdf-preview",
        action="store_true",
        help="also write <stem>_preview.pdf, the same drawing as a vector "
             "PDF, again without needing LaTeX",
    )
    parser.add_argument(
        "--print-alt",
        action="store_true",
        help="also print the generated alt text to stdout",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="suppress progress and 'wrote:' lines "
             "(warnings and errors still go to stderr)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _netlist_formats(parser: argparse.ArgumentParser,
                     value: str) -> tuple[str, ...]:
    """Parse --netlist into a tuple, exiting with usage on a bad name."""
    if not value.strip():
        return ()
    names = tuple(part.strip().lower() for part in value.split(",")
                  if part.strip())
    for name in names:
        if name not in pipeline.NETLIST_FORMATS:
            parser.error(
                f"unknown netlist format '{name}'; choose from "
                + ", ".join(pipeline.NETLIST_FORMATS))
    return names


def _default_output_dir(input_path: str) -> str:
    """Return ``<input folder>/accessible`` for *input_path*."""
    parent = os.path.dirname(input_path)
    return os.path.join(parent, "accessible") if parent else "accessible"


def _comparisons(parser: argparse.ArgumentParser,
                 args: argparse.Namespace
                 ) -> list[tuple[str, str, str | None]]:
    """Resolve the --compare-* / --generated-* flags into comparison jobs.

    Each job is ``(kind, KiCad's file, SchemAccess's file or None)``; None
    means convert the schematic and compare what that produces.
    """
    jobs: list[tuple[str, str, str | None]] = []
    for kind, reference, generated in (
            (netcompare.KIND_KICAD, args.compare_net, args.generated_net),
            (netcompare.KIND_SPICE, args.compare_cir, args.generated_cir)):
        if generated and not reference:
            parser.error(f"--generated-{kind} needs --compare-{kind}: there "
                         f"is nothing to compare it against")
        if reference:
            jobs.append((kind, reference, generated))
    return jobs


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _report_comparisons(comparisons: list[tuple[str, str, str | None]],
                        netlists: dict[str, str],
                        input_path: str | None) -> int:
    """Compare SchemAccess's netlists against KiCad's and print verdicts.

    Returns the exit code: 0 when every comparison passed.
    """
    worst = 0
    for kind, reference_path, generated_path in comparisons:
        try:
            reference = _read_text(reference_path)
            generated = (_read_text(generated_path) if generated_path
                         else netlists.get(_COMPARE_FORMATS[kind], ""))
        except OSError as exc:
            print(f"error: could not read {exc.filename or reference_path}: "
                  f"{exc}", file=sys.stderr)
            worst = 1
            continue

        # Comparing one schematic's netlist against another schematic's
        # produces differences that say nothing about either tool, so say
        # so rather than letting it look like a real disagreement.  Both
        # sides of a .net comparison record the schematic they came from,
        # so the check works with or without a schematic to convert.
        if kind == netcompare.KIND_KICAD:
            source = netcompare.netlist_source(reference)
            expected = (os.path.basename(input_path) if input_path
                        else netcompare.netlist_source(generated))
            if source and expected and source != expected:
                print(f"warning: {os.path.basename(reference_path)} was "
                      f"written from '{source}', not '{expected}'.",
                      file=sys.stderr)

        subject = (f"{reference_path} vs {generated_path}" if generated_path
                   else os.path.basename(reference_path))
        try:
            report = netcompare.compare(kind, reference, generated,
                                        subject=subject)
        except ValueError as exc:      # includes NetlistFormatError
            print(f"error: could not compare {reference_path}: {exc}",
                  file=sys.stderr)
            worst = 1
            continue

        print(report.format())
        if not report.ok:
            worst = 1
    return worst


def _ordered_output_files(files: dict[str, str]) -> list[str]:
    """Return output file paths in a stable, documented order."""
    ordered = [files[key] for key in _FILE_ORDER if key in files]
    ordered.extend(files[key] for key in sorted(files)
                   if key not in _FILE_ORDER)
    return ordered


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``schemaccess`` command.

    Returns the process exit code (0 on success, 1 on conversion errors);
    argparse itself exits with 2 for bad arguments.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    netlist_formats = _netlist_formats(parser, args.netlist)

    comparisons = _comparisons(parser, args)
    comparing = bool(comparisons)
    if comparing and args.check:
        print("warning: --compare-net/--compare-cir report on the netlists; "
              "--check is ignored.", file=sys.stderr)

    # A comparison handed both of its files needs nothing converted, so a
    # run of nothing but those needs no schematic at all.
    to_generate = tuple(_COMPARE_FORMATS[kind]
                        for kind, _, generated in comparisons
                        if generated is None)
    if comparing and not to_generate and not netlist_formats:
        return _report_comparisons(comparisons, {}, args.input)

    if args.input is None:
        parser.error("the schematic is required, unless every comparison is "
                     "given both of its files with --compare-* and "
                     "--generated-*")

    if comparing:
        # A comparison needs the netlist it compares, whether or not
        # --netlist asked for it.  Order is kept and duplicates dropped.
        netlist_formats = tuple(dict.fromkeys(netlist_formats + to_generate))
        # It is about the netlists, so the alt text and the drawing are
        # not generated: nothing else is being checked, nothing written.
        want_alt_text = want_image = False
    else:
        # --check converts both outputs in memory so it can report on
        # them, regardless of the other switches.
        want_alt_text = args.check or not args.no_alt_text
        want_image = args.check or not args.no_image

    output_dir = args.output_dir or _default_output_dir(args.input)
    options = pipeline.PipelineOptions(
        input_path=args.input,
        output_dir=output_dir,
        generate_alt_text=want_alt_text,
        generate_image=want_image,
        export_format=args.format,
        detail_level="detailed" if args.check else args.detail,
        junction_dots=not args.no_junction_dots,
        switch_style=args.switch_style,
        show_loops=args.loops,
        netlist_formats=netlist_formats,
        svg_preview=args.svg_preview and not comparing,
        pdf_preview=args.pdf_preview and not comparing,
        dry_run=args.check or comparing,
    )

    if comparing:
        pass                    # --check was already reported as ignored
    elif args.check and (args.no_alt_text or args.no_image):
        print("warning: --check reports on both outputs; --no-alt-text and "
              "--no-image are ignored.", file=sys.stderr)
    elif args.no_alt_text and args.no_image:
        print("warning: both alt text and image generation are disabled; "
              "the schematic will only be parsed and checked.",
              file=sys.stderr)

    progress = None if args.quiet else (lambda msg: print(msg))
    try:
        result = pipeline.run_pipeline(options, progress=progress)
    except Exception as exc:  # stubs / unexpected bugs: no tracebacks
        message = str(exc) or exc.__class__.__name__
        print(f"error: {message}", file=sys.stderr)
        return 1

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if not result.ok:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    if comparing:
        return _report_comparisons(comparisons, result.netlists, args.input)

    if args.check:
        stats = result.stats
        missed = stats.fallbacks or stats.undescribed
        if missed:
            print("error: some components did not convert cleanly.",
                  file=sys.stderr)
            return 1
        if not args.quiet:
            print("All components converted.")
        return 0

    if not args.quiet:
        for path in _ordered_output_files(result.output_files):
            print(f"wrote: {path}")

    if args.print_alt:
        if result.alt_text:
            print()
            print(result.alt_text)
        else:
            print("warning: --print-alt requested but no alt text was "
                  "generated (alt text is disabled).", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
