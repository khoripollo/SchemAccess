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

from . import __version__, netlist, pipeline

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

examples:
  schemaccess board.kicad_sch
  schemaccess board.kicad_sch -o out --format svg --detail detailed
  schemaccess board.kicad_sch --no-image --print-alt --quiet
  schemaccess board.kicad_sch --no-junction-dots --format pdf
  schemaccess board.kicad_sch --loops --detail detailed
  schemaccess board.kicad_sch --units --format pdf
  schemaccess board.kicad_sch --netlist all --svg-preview --pdf-preview
  schemaccess board.kicad_sch --netlist spice,csv --no-image
"""


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
        type=_existing_file,
        help="path to the KiCad schematic (.kicad_sch) to convert",
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
        "--loops",
        action="store_true",
        help="for teaching mesh analysis: draw each loop current (i1, i2, "
             "...) as an arrow in its window, turning the way its current "
             "flows, and describe the loops in the alt text.  Works on flat "
             "circuits of two-terminal parts with up to 4 loops; any other "
             "circuit is converted without them and a warning says why",
    )
    parser.add_argument(
        "--units",
        action="store_true",
        help="write units after the values on the drawing (1 -> 1 H, "
             "22n -> 22 nF, 100k -> 100 kΩ)",
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

    output_dir = args.output_dir or _default_output_dir(args.input)
    options = pipeline.PipelineOptions(
        input_path=args.input,
        output_dir=output_dir,
        # --check converts both outputs in memory so it can report on
        # them, regardless of the other switches.
        generate_alt_text=args.check or not args.no_alt_text,
        generate_image=args.check or not args.no_image,
        export_format=args.format,
        detail_level="detailed" if args.check else args.detail,
        junction_dots=not args.no_junction_dots,
        show_loops=args.loops,
        show_units=args.units,
        netlist_formats=netlist_formats,
        svg_preview=args.svg_preview,
        pdf_preview=args.pdf_preview,
        dry_run=args.check,
    )

    if args.check and (args.no_alt_text or args.no_image):
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
