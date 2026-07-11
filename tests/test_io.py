"""I/O-1: file loading.

Every valid fixture in the manifest parses without error into a non-empty
document; malformed files, non-schematic files, missing paths and
directories all raise :class:`~schemaccess.kicad_parser.KiCadParseError`.
"""

from __future__ import annotations

import pytest

from conftest import VALID_FIXTURES
from schemaccess import kicad_parser


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_io1_valid_files_load(name: str, fixtures_dir) -> None:
    """Every manifest fixture parses and yields a non-empty document."""
    doc = kicad_parser.parse_file(str(fixtures_dir / name))
    assert doc is not None
    assert doc.symbols, f"{name} parsed to a document with no symbols"
    assert doc.lib_symbols, f"{name} parsed with no library symbols"


def test_io1_malformed_file_raises(fixtures_dir) -> None:
    """A truncated/broken s-expression file raises KiCadParseError."""
    with pytest.raises(kicad_parser.KiCadParseError):
        kicad_parser.parse_file(str(fixtures_dir / "malformed.kicad_sch"))


def test_io1_not_a_schematic_raises(fixtures_dir) -> None:
    """A well-formed file that is not a schematic raises KiCadParseError."""
    with pytest.raises(kicad_parser.KiCadParseError):
        kicad_parser.parse_file(
            str(fixtures_dir / "not_a_schematic.kicad_sch"))


def test_io1_missing_path_raises(fixtures_dir) -> None:
    """A nonexistent path raises KiCadParseError (not FileNotFoundError)."""
    with pytest.raises(kicad_parser.KiCadParseError):
        kicad_parser.parse_file(
            str(fixtures_dir / "definitely_not_here.kicad_sch"))


def test_io1_directory_raises(fixtures_dir) -> None:
    """Passing a directory raises KiCadParseError."""
    with pytest.raises(kicad_parser.KiCadParseError):
        kicad_parser.parse_file(str(fixtures_dir))
