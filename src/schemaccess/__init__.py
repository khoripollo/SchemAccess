"""SchemAccess: accessible schematics from KiCad files.

Converts KiCad ``.kicad_sch`` schematics into:

* structured natural-language descriptions (alt text) for screen readers, and
* CircuiTikZ/LaTeX source compilable to PDF/SVG/PNG.

Pipeline:  kicad_parser -> netbuilder -> analyzer -> (alttext | circuitikz) -> renderer
"""

__version__ = "1.0.0"
