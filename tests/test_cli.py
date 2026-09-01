"""Command-line interface tests.

``--version`` and ``--help`` exit 0; a real conversion run with
``--no-image`` prints the pipeline stages and exits 0.
"""

from __future__ import annotations

import pytest

from schemaccess import __version__, cli


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
