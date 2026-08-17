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

from . import __version__, pipeline

#: Preferred, deterministic ordering for the "wrote:" report lines.
_FILE_ORDER = ("alt_text", "tex", "pdf", "svg", "png")

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
        "--no-junction-dots",
        action="store_true",
        help="omit the connection dots drawn where wires meet "
             "(they are included by default, as KiCad draws them)",
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

    output_dir = args.output_dir or _default_output_dir(args.input)
    options = pipeline.PipelineOptions(
        input_path=args.input,
        output_dir=output_dir,
        generate_alt_text=not args.no_alt_text,
        generate_image=not args.no_image,
        export_format=args.format,
        detail_level=args.detail,
        junction_dots=not args.no_junction_dots,
    )

    if args.no_alt_text and args.no_image:
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
