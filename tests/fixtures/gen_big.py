"""Deterministic generator for the ``big_200.kicad_sch`` stress fixture.

Writes an RC ladder of 100 identical stages into the same folder as this
script.  Each stage k (1-based) has a horizontal resistor ``R<k>`` in series
with the previous stage and a vertical capacitor ``C<k>`` shunting the stage
output node to its own ``power:GND`` symbol: 200 real components plus 100
power symbols in total.  All coordinates are exact multiples of 1.27 mm and
every wire endpoint lands exactly on a pin position, so the netbuilder
produces no connectivity warnings.  The script uses no randomness and no
timestamps; running it twice produces byte-identical output.

Usage::

    python gen_big.py
"""

from __future__ import annotations

import os
from typing import List

# Geometry in hundredths of a millimetre (integers avoid float drift).
# 127 = 1.27 mm grid unit.
BASE_X = 4064          # x of R1's body centre (40.64 mm)
PITCH = 2032           # stage-to-stage spacing (20.32 mm)
Y_RAIL = 7620          # y of the horizontal ladder rail (76.20 mm)
PIN_HALF = 381         # resistor/capacitor pin offset from body (3.81 mm)
NODE_DX = 1016         # stage node sits 10.16 mm right of the R body
Y_C = 8382             # capacitor body centre y (83.82 mm)
Y_GND = 9144           # ground symbol y (91.44 mm)
N_STAGES = 100

DOC_UUID = "c0000000-0001-4000-8000-000000000001"

LIB_SYMBOLS = """  (lib_symbols
    (symbol "Device:R" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
      (property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "R_0_1"
        (rectangle (start -1.016 -2.54) (end 1.016 2.54)
          (stroke (width 0.254) (type default)) (fill (type none)))
      )
      (symbol "R_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
    (symbol "Device:C" (pin_numbers hide) (pin_names (offset 0.254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 0.635 2.54 0) (effects (font (size 1.27 1.27))))
      (property "Value" "C" (at 0.635 -2.54 0) (effects (font (size 1.27 1.27))))
      (property "Description" "Unpolarized capacitor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "C_0_1"
        (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762))
          (stroke (width 0.508) (type default)) (fill (type none)))
        (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762))
          (stroke (width 0.508) (type default)) (fill (type none)))
      )
      (symbol "C_1_1"
        (pin passive line (at 0 3.81 270) (length 2.794)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 2.794)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
    (symbol "power:GND" (power) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -6.35 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "GND" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
      (property "Description" "Power symbol: ground" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "GND_0_1"
        (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 270) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
    )
  )"""


def fmt(cent: int) -> str:
    """Format a coordinate given in hundredths of a millimetre.

    Produces the compact decimal KiCad style: ``7620 -> '76.2'``,
    ``4064 -> '40.64'``, ``12700 -> '127'``.
    """
    whole, frac = divmod(cent, 100)
    if frac == 0:
        return str(whole)
    text = f"{whole}.{frac:02d}"
    return text.rstrip("0")


class _UuidFactory:
    """Sequential, unique, deterministic uuid-shaped strings."""

    def __init__(self) -> None:
        self._counter = 0

    def next(self) -> str:
        self._counter += 1
        return f"c0000000-0002-4000-8000-{self._counter:012d}"


def _wire(out: List[str], uid: _UuidFactory,
          x1: int, y1: int, x2: int, y2: int) -> None:
    out.append(
        f"  (wire (pts (xy {fmt(x1)} {fmt(y1)}) (xy {fmt(x2)} {fmt(y2)}))\n"
        f"    (stroke (width 0) (type default)) (uuid \"{uid.next()}\"))")


def _junction(out: List[str], uid: _UuidFactory, x: int, y: int) -> None:
    out.append(
        f"  (junction (at {fmt(x)} {fmt(y)}) (diameter 0) (color 0 0 0 0)\n"
        f"    (uuid \"{uid.next()}\"))")


def _resistor(out: List[str], uid: _UuidFactory, ref: str,
              x: int, y: int) -> None:
    """A Device:R rotated 90 degrees (horizontal; pin 1 left, pin 2 right)."""
    out.append(f"""  (symbol (lib_id "Device:R") (at {fmt(x)} {fmt(y)} 90) (unit 1)
    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (uuid "{uid.next()}")
    (property "Reference" "{ref}" (at {fmt(x)} {fmt(y - 508)} 0) (effects (font (size 1.27 1.27))))
    (property "Value" "1k" (at {fmt(x)} {fmt(y - 254)} 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at {fmt(x)} {fmt(y)} 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (pin "1" (uuid "{uid.next()}"))
    (pin "2" (uuid "{uid.next()}"))
    (instances (project "big_200"
      (path "/{DOC_UUID}"
        (reference "{ref}") (unit 1))))
  )""")


def _capacitor(out: List[str], uid: _UuidFactory, ref: str,
               x: int, y: int) -> None:
    """A Device:C at angle 0 (vertical; pin 1 top, pin 2 bottom)."""
    out.append(f"""  (symbol (lib_id "Device:C") (at {fmt(x)} {fmt(y)} 0) (unit 1)
    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (uuid "{uid.next()}")
    (property "Reference" "{ref}" (at {fmt(x + 406)} {fmt(y - 127)} 0) (effects (font (size 1.27 1.27))))
    (property "Value" "100nF" (at {fmt(x + 406)} {fmt(y + 127)} 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at {fmt(x)} {fmt(y)} 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (pin "1" (uuid "{uid.next()}"))
    (pin "2" (uuid "{uid.next()}"))
    (instances (project "big_200"
      (path "/{DOC_UUID}"
        (reference "{ref}") (unit 1))))
  )""")


def _ground(out: List[str], uid: _UuidFactory, ref: str,
            x: int, y: int) -> None:
    out.append(f"""  (symbol (lib_id "power:GND") (at {fmt(x)} {fmt(y)} 0) (unit 1)
    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (uuid "{uid.next()}")
    (property "Reference" "{ref}" (at {fmt(x)} {fmt(y + 635)} 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Value" "GND" (at {fmt(x)} {fmt(y + 381)} 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid "{uid.next()}"))
    (instances (project "big_200"
      (path "/{DOC_UUID}"
        (reference "{ref}") (unit 1))))
  )""")


def build() -> str:
    """Assemble the complete ``big_200.kicad_sch`` text."""
    uid = _UuidFactory()
    parts: List[str] = []
    parts.append('(kicad_sch (version 20231120) (generator "eeschema") '
                 '(generator_version "8.0")')
    parts.append(f'  (uuid "{DOC_UUID}")')
    parts.append('  (paper "A4")')
    parts.append("  (title_block")
    parts.append('    (title "RC ladder, 100 stages")')
    parts.append('    (comment 1 "Generated by gen_big.py: 100 series '
                 'resistors, 100 shunt capacitors, one GND per stage")')
    parts.append("  )")
    parts.append(LIB_SYMBOLS)

    wires: List[str] = []
    junctions: List[str] = []
    symbols: List[str] = []

    for k in range(1, N_STAGES + 1):
        x_r = BASE_X + (k - 1) * PITCH          # resistor body centre x
        x_n = x_r + NODE_DX                     # stage output node x
        r_pin1_x = x_r - PIN_HALF
        r_pin2_x = x_r + PIN_HALF

        _resistor(symbols, uid, f"R{k}", x_r, Y_RAIL)
        _capacitor(symbols, uid, f"C{k}", x_n, Y_C)
        _ground(symbols, uid, f"#PWR{k:03d}", x_n, Y_GND)

        # Rail: R pin 2 to the stage node.
        _wire(wires, uid, r_pin2_x, Y_RAIL, x_n, Y_RAIL)
        # Stage node down to C pin 1.
        _wire(wires, uid, x_n, Y_RAIL, x_n, Y_C - PIN_HALF)
        # C pin 2 down to the stage's GND symbol.
        _wire(wires, uid, x_n, Y_C + PIN_HALF, x_n, Y_GND)
        # Stage node across to the next stage's R pin 1.
        if k < N_STAGES:
            next_pin1_x = x_r + PITCH - PIN_HALF
            _wire(wires, uid, x_n, Y_RAIL, next_pin1_x, Y_RAIL)
            # Three wires meet at the stage node.
            _junction(junctions, uid, x_n, Y_RAIL)

    parts.append("")
    parts.extend(junctions)
    parts.append("")
    parts.extend(wires)
    parts.append("")
    # The ladder input (R1 pin 1) is an intentional open port.
    parts.append(f'  (no_connect (at {fmt(BASE_X - PIN_HALF)} {fmt(Y_RAIL)}) '
                 f'(uuid "{uid.next()}"))')
    parts.append("")
    parts.extend(symbols)
    parts.append('  (sheet_instances (path "/" (page "1")))')
    parts.append(")")
    return "\n".join(parts) + "\n"


def main() -> None:
    """Write ``big_200.kicad_sch`` next to this script."""
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "big_200.kicad_sch")
    text = build()
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"Wrote {target} ({len(text)} bytes)")


if __name__ == "__main__":
    main()
