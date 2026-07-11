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
