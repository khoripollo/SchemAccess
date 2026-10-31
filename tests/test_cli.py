"""Command-line interface tests.

``--version`` and ``--help`` exit 0; a real conversion run with
``--no-image`` prints the pipeline stages and exits 0; and
``--compare-net``/``--compare-cir`` validate a conversion against a
netlist exported from KiCad without writing anything.

The comparison tests use a reference netlist written out here rather than
one produced by KiCad, so they stay fast and need no KiCad installed;
``tests/test_kicad_reference.py`` is where the real exports are used.
"""

from __future__ import annotations

import pytest

from conftest import load_graph
from schemaccess import __version__, cli, netlist

# What KiCad exports for tests/fixtures/voltage_divider.kicad_sch: a +5V
# rail through two 10k resistors to GND, with the midpoint labelled VOUT.
# KiCad's own names for those nets ('/VOUT' for a local label, 'GND' from
# the power symbol) differ from SchemAccess's on purpose.
VOLTAGE_DIVIDER_NET = """\
(export (version "E")
  (design
    (source "/home/someone/voltage_divider.kicad_sch")
    (tool "Eeschema 10.0.1"))
  (components
    (comp (ref "R1")
      (value "10k")
      (libsource (lib "Device") (part "R") (description "Resistor")))
    (comp (ref "R2")
      (value "10k")
      (libsource (lib "Device") (part "R") (description "Resistor"))))
  (nets
    (net (code "1") (name "+5V")
      (node (ref "R1") (pin "1") (pintype "passive")))
    (net (code "2") (name "/VOUT")
      (node (ref "R1") (pin "2") (pintype "passive"))
      (node (ref "R2") (pin "1") (pintype "passive")))
    (net (code "3") (name "GND")
      (node (ref "R2") (pin "2") (pintype "passive")))))
"""

VOLTAGE_DIVIDER_CIR = """\
.title KiCad schematic
R1 +5V /VOUT 10k
R2 /VOUT GND 10k
.end
"""


def test_cli_version_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "schemaccess" in out
    assert __version__ in out


def test_cli_help_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "kicad_sch" in out


def test_cli_real_run_no_image_prints_stages(fixtures_dir, tmp_path,
                                             capsys) -> None:
    """A real run on rc_divider with --no-image succeeds and logs stages."""
    exit_code = cli.main([
        str(fixtures_dir / "rc_divider.kicad_sch"),
        "-o", str(tmp_path),
        "--no-image",
    ])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    for stage in ("Reading KiCad schematic...",
                  "Parsing components...",
                  "Generating connectivity graph...",
                  "Creating alt text...",
                  "Done."):
        assert stage in captured.out, f"stage line missing: {stage}"
    assert "wrote:" in captured.out
    alt_file = tmp_path / "rc_divider_alt_text.txt"
    assert alt_file.is_file() and alt_file.stat().st_size > 0
    # --no-image: no .tex or rendered outputs.
    assert not (tmp_path / "rc_divider.tex").exists()


def test_cli_missing_input_exits_two(capsys) -> None:
    """argparse rejects a nonexistent input file with exit code 2."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["no_such_file.kicad_sch"])
    assert excinfo.value.code == 2


def test_cli_check_reports_without_writing_files(tmp_path, capsys,
                                                 fixtures_dir) -> None:
    """--check converts in memory and reports; it must not write output."""
    out = tmp_path / "should_stay_empty"
    code = cli.main([str(fixtures_dir / "rc_divider.kicad_sch"),
                     "-o", str(out), "--check"])
    printed = capsys.readouterr().out

    assert code == 0
    assert not out.exists(), "--check must not create the output folder"
    assert "4 components in the KiCad schematic" in printed
    assert "4 of 4 components converted to CircuiTikZ symbols." in printed
    assert "4 of 4 components described in the alt text." in printed
    assert "Converted in" in printed and "ms" in printed
    assert "All components converted." in printed


def test_cli_check_reports_both_outputs_despite_no_flags(tmp_path, capsys,
                                                         fixtures_dir) -> None:
    """--check always reports on the drawing and the description, so the
    counts are never silently missing."""
    code = cli.main([str(fixtures_dir / "rc_divider.kicad_sch"),
                     "-o", str(tmp_path / "nope"), "--check",
                     "--no-image", "--no-alt-text"])
    captured = capsys.readouterr()

    assert code == 0
    assert "converted to CircuiTikZ symbols." in captured.out
    assert "described in the alt text." in captured.out
    assert "ignored" in captured.err, "the conflict should be explained"


# ---------------------------------------------------------------------------
# Validating a conversion against KiCad's own netlists
# ---------------------------------------------------------------------------

def _reference(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_compare_net_passes_and_writes_nothing(tmp_path, capsys,
                                               fixtures_dir) -> None:
    """A conversion that agrees with KiCad's netlist exits 0."""
    out = tmp_path / "should_stay_empty"
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"),
                     "-o", str(out),
                     "--compare-net",
                     _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET)])
    printed = capsys.readouterr().out

    assert code == 0
    assert not out.exists(), "a comparison must not create the output folder"
    assert printed.startswith("PASS  ref.net") or "PASS  ref.net" in printed
    assert "2 component(s)" in printed
    assert "net(s) joining the same pins on both sides" in printed


def test_compare_both_formats_reports_each(tmp_path, capsys,
                                           fixtures_dir) -> None:
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"),
                     "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET),
                     "--compare-cir",
                     _reference(tmp_path, "ref.cir", VOLTAGE_DIVIDER_CIR)])
    printed = capsys.readouterr().out

    assert code == 0, printed
    assert "PASS  ref.net" in printed
    assert "PASS  ref.cir" in printed
    assert "SPICE node 0" in printed


def test_compare_net_fails_and_names_the_difference(tmp_path, capsys,
                                                    fixtures_dir) -> None:
    """A value KiCad disagrees about fails the run and is named."""
    damaged = VOLTAGE_DIVIDER_NET.replace('(value "10k")', '(value "47k")', 1)
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"), "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", damaged)])
    printed = capsys.readouterr().out

    assert code == 1
    assert "FAIL  ref.net" in printed
    assert "R1" in printed and "47k" in printed


def test_compare_net_detects_rewiring(tmp_path, capsys, fixtures_dir) -> None:
    """Net names are ignored, but the pins a net joins are not."""
    damaged = VOLTAGE_DIVIDER_NET.replace(
        '(node (ref "R2") (pin "1") (pintype "passive"))',
        '(node (ref "R2") (pin "2") (pintype "passive"))', 1)
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"), "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", damaged)])
    printed = capsys.readouterr().out

    assert code == 1
    assert "connectivity" in printed


def test_renaming_every_net_is_not_a_difference(tmp_path, capsys,
                                                fixtures_dir) -> None:
    """The reference's net names carry no weight at all."""
    renamed = (VOLTAGE_DIVIDER_NET.replace('"+5V"', '"RAIL"')
               .replace('"/VOUT"', '"Net-(R1-Pad2)"'))
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"), "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", renamed)])
    printed = capsys.readouterr().out
    assert code == 0, printed


def test_compare_warns_when_the_reference_is_another_schematic(
        tmp_path, capsys, fixtures_dir) -> None:
    """Comparing against the wrong export is a mistake worth naming."""
    cli.main([str(fixtures_dir / "rc_divider.kicad_sch"), "-q",
              "--compare-net",
              _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET)])
    captured = capsys.readouterr()
    assert "was written from 'voltage_divider.kicad_sch'" in captured.err


def test_compare_rejects_a_file_that_is_not_a_netlist(tmp_path, capsys,
                                                      fixtures_dir) -> None:
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"), "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", "just some text")])
    captured = capsys.readouterr()
    assert code == 1
    assert "could not compare" in captured.err


def test_compare_needs_an_existing_reference(fixtures_dir) -> None:
    """argparse rejects a reference that is not there."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"),
                  "--compare-net", "no_such_file.net"])
    assert excinfo.value.code == 2


def test_compare_two_files_without_converting_anything(tmp_path, capsys,
                                                       fixtures_dir) -> None:
    """--generated-net compares a netlist already on disk.

    No schematic is needed: nothing has to be converted, so the command
    line carries only the two files.
    """
    mine = tmp_path / "accessible" / "voltage_divider.net"
    mine.parent.mkdir()
    mine.write_text(
        netlist.generate(load_graph("voltage_divider.kicad_sch"),
                         "kicad").text,
        encoding="utf-8")

    code = cli.main(["--compare-net",
                     _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET),
                     "--generated-net", str(mine)])
    printed = capsys.readouterr().out

    assert code == 0, printed
    assert printed.startswith("PASS")
    # Both paths are named, since the two files often share a basename.
    assert "ref.net" in printed and str(mine) in printed
    assert "net(s) joining the same pins on both sides" in printed


def test_comparing_two_files_still_catches_a_difference(tmp_path, capsys,
                                                        fixtures_dir) -> None:
    mine = tmp_path / "mine.net"
    mine.write_text(
        netlist.generate(load_graph("rc_divider.kicad_sch"), "kicad").text,
        encoding="utf-8")

    code = cli.main(["--compare-net",
                     _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET),
                     "--generated-net", str(mine)])
    captured = capsys.readouterr()

    assert code == 1
    assert "FAIL" in captured.out
    # Both files record which schematic they came from, so the mismatch
    # is noticed without a schematic on the command line.
    assert "was written from 'voltage_divider.kicad_sch'" in captured.err


def test_a_generated_file_without_its_reference_is_rejected(tmp_path,
                                                            fixtures_dir
                                                            ) -> None:
    mine = _reference(tmp_path, "mine.net", VOLTAGE_DIVIDER_NET)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--generated-net", mine])
    assert excinfo.value.code == 2


def test_the_schematic_is_required_when_something_must_be_converted(
        tmp_path) -> None:
    """Leaving it off is only allowed when nothing needs converting."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--compare-net",
                  _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET)])
    assert excinfo.value.code == 2

    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_one_side_from_a_file_and_one_regenerated(tmp_path, capsys,
                                                  fixtures_dir) -> None:
    """The two comparisons are independent; mixing the modes is fine."""
    mine = tmp_path / "mine.net"
    mine.write_text(
        netlist.generate(load_graph("voltage_divider.kicad_sch"),
                         "kicad").text,
        encoding="utf-8")

    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"), "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET),
                     "--generated-net", str(mine),
                     "--compare-cir",
                     _reference(tmp_path, "ref.cir", VOLTAGE_DIVIDER_CIR)])
    printed = capsys.readouterr().out

    assert code == 0, printed
    assert printed.count("PASS") == 2
    assert "SPICE node 0" in printed


def test_compare_takes_precedence_over_check(tmp_path, capsys,
                                             fixtures_dir) -> None:
    code = cli.main([str(fixtures_dir / "voltage_divider.kicad_sch"),
                     "--check", "-q",
                     "--compare-net",
                     _reference(tmp_path, "ref.net", VOLTAGE_DIVIDER_NET)])
    captured = capsys.readouterr()

    assert code == 0
    assert "PASS  ref.net" in captured.out
    assert "All components converted." not in captured.out
    assert "--check is ignored" in captured.err
