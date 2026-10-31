"""SchemAccess: accessible schematics from KiCad files.

Converts KiCad ``.kicad_sch`` schematics into:

* structured natural-language descriptions (alt text) for screen readers, and
* CircuiTikZ/LaTeX source compilable to PDF/SVG/PNG.

Pipeline:  kicad_parser -> netbuilder -> analyzer -> (alttext | circuitikz) -> renderer

``netcompare`` sits beside that pipeline rather than in it: it holds a
generated netlist against one KiCad exported itself, so KiCad can be used
as an independent check that the schematic was read correctly.
"""

__version__ = "1.0.0"
