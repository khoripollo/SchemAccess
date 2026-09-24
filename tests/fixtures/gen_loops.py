"""Writes the loops_*.kicad_sch fixtures: python gen_loops.py"""

from __future__ import annotations

import os
from typing import List, Tuple

PITCH = 3810
X0 = 5080
Y_TOP = 6096
Y_MID = 7620
Y_BOT = 9144
R_HALF = 381
V_HALF = 508

VALUES = ["100", "220", "330", "470", "680", "1k", "2k2", "3k3", "4k7",
          "5k6", "6k8", "8k2", "10k", "12k", "15k"]

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
    (symbol "Simulation_SPICE:VDC" (pin_numbers hide) (pin_names (offset 0.0254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "V" (at 2.54 2.54 0) (effects (font (size 1.27 1.27))))
      (property "Value" "VDC" (at 2.54 0 0) (effects (font (size 1.27 1.27))))
      (property "Description" "Voltage source, DC" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "VDC_0_1"
        (circle (center 0 0) (radius 2.54)
          (stroke (width 0.254) (type default)) (fill (type none)))
      )
      (symbol "VDC_1_1"
        (pin passive line (at 0 5.08 270) (length 2.54)
          (name "+" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -5.08 90) (length 2.54)
          (name "-" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
    (symbol "Simulation_SPICE:IDC" (pin_numbers hide) (pin_names (offset 0.0254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "I" (at 2.54 2.54 0) (effects (font (size 1.27 1.27))))
      (property "Value" "IDC" (at 2.54 0 0) (effects (font (size 1.27 1.27))))
      (property "Description" "Current source, DC" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Sim.Pins" "1=+ 2=-" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "IDC_0_1"
        (circle (center 0 0) (radius 2.54)
          (stroke (width 0.254) (type default)) (fill (type none)))
        (polyline (pts (xy 0 1.27) (xy 0 -1.27) (xy 0.508 -0.508) (xy -0.508 -0.508) (xy 0 -1.27))
          (stroke (width 0.1524) (type default)) (fill (type none)))
      )
      (symbol "IDC_1_1"
        (pin passive line (at 0 5.08 270) (length 2.54)
          (name "+" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -5.08 90) (length 2.54)
          (name "-" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
    (symbol "Device:D" (pin_numbers hide) (pin_names (offset 1.016) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "D" (at 0 2.54 0) (effects (font (size 1.27 1.27))))
      (property "Value" "D" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))
      (property "Description" "Diode" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "D_0_1"
        (polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27))
          (stroke (width 0.254) (type default)) (fill (type none)))
        (polyline (pts (xy 1.27 1.27) (xy 1.27 -1.27) (xy -1.27 0) (xy 1.27 1.27))
          (stroke (width 0.254) (type default)) (fill (type none)))
      )
      (symbol "D_1_1"
        (pin passive line (at -3.81 0 0) (length 2.54)
          (name "K" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 3.81 0 180) (length 2.54)
          (name "A" (effects (font (size 1.27 1.27))))
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
    whole, frac = divmod(cent, 100)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:02d}".rstrip("0")


class _Sheet:
    def __init__(self, name: str, serial: int) -> None:
        self.name = name
        self.doc_uuid = f"c0000000-0010-4000-8000-{serial:012d}"
        self._serial = serial
        self._counter = 0
        self.items: List[str] = []

    def uid(self) -> str:
        self._counter += 1
        return f"c0000000-{self._serial:04d}-4000-8000-{self._counter:012d}"

    def wire(self, a: Tuple[int, int], b: Tuple[int, int]) -> None:
        self.items.append(
            f"  (wire (pts (xy {fmt(a[0])} {fmt(a[1])}) "
            f"(xy {fmt(b[0])} {fmt(b[1])}))\n"
            f"    (stroke (width 0) (type default)) (uuid \"{self.uid()}\"))")

    def junction(self, p: Tuple[int, int]) -> None:
        self.items.append(
            f"  (junction (at {fmt(p[0])} {fmt(p[1])}) (diameter 0) "
            f"(color 0 0 0 0)\n    (uuid \"{self.uid()}\"))")

    def _symbol(self, lib_id: str, ref: str, value: str, x: int, y: int,
                angle: int, pins: int, hide_value: bool = False) -> None:
        hide = " (hide yes)" if hide_value else ""
        pin_lines = "\n".join(f'    (pin "{n}" (uuid "{self.uid()}"))'
                              for n in range(1, pins + 1))
        self.items.append(f"""  (symbol (lib_id "{lib_id}") (at {fmt(x)} {fmt(y)} {angle}) (unit 1)
    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (uuid "{self.uid()}")
    (property "Reference" "{ref}" (at {fmt(x + 254)} {fmt(y - 127)} 0) (effects (font (size 1.27 1.27)){' (hide yes)' if ref.startswith('#') else ''}))
    (property "Value" "{value}" (at {fmt(x + 254)} {fmt(y + 127)} 0) (effects (font (size 1.27 1.27)){hide}))
    (property "Footprint" "" (at {fmt(x)} {fmt(y)} 0) (effects (font (size 1.27 1.27)) (hide yes)))
{pin_lines}
    (instances (project "{self.name}"
      (path "/{self.doc_uuid}"
        (reference "{ref}") (unit 1))))
  )""")

    def resistor(self, ref: str, value: str, x: int, y: int,
                 lying: bool = False) -> Tuple[Tuple[int, int],
                                               Tuple[int, int]]:
        self._symbol("Device:R", ref, value, x, y, 90 if lying else 0, 2)
        if lying:
            return (x - R_HALF, y), (x + R_HALF, y)
        return (x, y - R_HALF), (x, y + R_HALF)

    def source(self, ref: str, value: str, x: int, y: int):
        self._symbol("Simulation_SPICE:VDC", ref, value, x, y, 0, 2)
        return (x, y - V_HALF), (x, y + V_HALF)

    def current_source(self, ref: str, value: str, x: int, y: int):
        self._symbol("Simulation_SPICE:IDC", ref, value, x, y, 0, 2)
        return (x, y - V_HALF), (x, y + V_HALF)

    def diode(self, ref: str, value: str, x: int, y: int):
        self._symbol("Device:D", ref, value, x, y, 0, 2)
        return (x - R_HALF, y), (x + R_HALF, y)

    def ground(self, ref: str, x: int, y: int) -> None:
        self._symbol("power:GND", ref, "GND", x, y, 0, 1)

    def text(self, title: str) -> str:
        head = [
            '(kicad_sch (version 20231120) (generator "eeschema") '
            '(generator_version "8.0")',
            f'  (uuid "{self.doc_uuid}")',
            '  (paper "A3")',
            "  (title_block",
            f'    (title "{title}")',
            '    (comment 1 "Generated by gen_loops.py")',
            "  )",
            LIB_SYMBOLS,
        ]
        tail = ['  (sheet_instances (path "/" (page "1")))', ")", ""]
        return "\n".join(head + self.items + tail)


def ladder(sheet: _Sheet, windows: int, bottom: str = "wire") -> None:
    plus, minus = sheet.source("V1", "10V", X0, Y_MID)
    sheet.wire(plus, (X0, Y_TOP))
    columns = [X0 + k * PITCH for k in range(windows + 1)]
    bottoms = [minus]
    for k in range(1, windows + 1):
        left, right = columns[k - 1], columns[k]
        a, b = sheet.resistor(f"R{2 * k - 1}", VALUES[2 * k - 2],
                              left + PITCH // 2, Y_TOP, lying=True)
        sheet.wire((left, Y_TOP), a)
        sheet.wire(b, (right, Y_TOP))
        top, low = sheet.resistor(f"R{2 * k}", VALUES[2 * k - 1],
                                  right, Y_MID)
        sheet.wire((right, Y_TOP), top)
        bottoms.append(low)
        if k < windows:
            sheet.junction((right, Y_TOP))
    if bottom == "wire":
        sheet.wire(minus, (X0, Y_BOT))
        for k in range(1, windows + 1):
            sheet.wire(bottoms[k], (columns[k], Y_BOT))
            sheet.wire((columns[k - 1], Y_BOT), (columns[k], Y_BOT))
            if k < windows:
                sheet.junction((columns[k], Y_BOT))
    else:
        for k, low in enumerate(bottoms):
            y = Y_BOT + (1270 if k == windows else 0)
            sheet.wire(low, (columns[k], y))
            sheet.ground(f"#PWR0{k + 1}", columns[k], y)


def grid(sheet: _Sheet) -> None:
    xs = [5080, 8890, 12700]
    ys = [5080, 8890, 12700]
    mid_y = [(ys[0] + ys[1]) // 2, (ys[1] + ys[2]) // 2]
    mid_x = [(xs[0] + xs[1]) // 2, (xs[1] + xs[2]) // 2]
    plus, minus = sheet.source("V1", "12V", xs[0], mid_y[0])
    sheet.wire((xs[0], ys[0]), plus)
    sheet.wire(minus, (xs[0], ys[1]))
    uprights = [("R1", 0, 1), ("R2", 1, 0), ("R3", 1, 1), ("R4", 2, 0)]
    for ref, col, row in uprights:
        a, b = sheet.resistor(ref, VALUES[int(ref[1:]) - 1], xs[col],
                              mid_y[row])
        sheet.wire((xs[col], ys[row]), a)
        sheet.wire(b, (xs[col], ys[row + 1]))
    sheet.wire((xs[2], ys[1]), (xs[2], ys[2]))
    lying = [("R5", 0, 0), ("R6", 1, 0), ("R7", 0, 1), ("R8", 1, 1),
             ("R9", 1, 2)]
    for ref, col, row in lying:
        a, b = sheet.resistor(ref, VALUES[int(ref[1:]) - 1], mid_x[col],
                              ys[row], lying=True)
        sheet.wire((xs[col], ys[row]), a)
        sheet.wire(b, (xs[col + 1], ys[row]))
    sheet.wire((xs[0], ys[2]), (xs[1], ys[2]))
    for point in ((xs[1], ys[0]), (xs[0], ys[1]), (xs[1], ys[1]),
                  (xs[2], ys[1]), (xs[1], ys[2])):
        sheet.junction(point)


def network(sheet: _Sheet, uprights, tops) -> None:
    columns = [X0 + k * PITCH for k in range(len(uprights))]
    for x, (kind, ref, value) in zip(columns, uprights):
        if kind == "V":
            top, low = sheet.source(ref, value, x, Y_MID)
        elif kind == "I":
            top, low = sheet.current_source(ref, value, x, Y_MID)
        else:
            top, low = sheet.resistor(ref, value, x, Y_MID)
        sheet.wire((x, Y_TOP), top)
        sheet.wire(low, (x, Y_BOT))
    for k, (kind, ref, value) in enumerate(tops, start=1):
        left, right = columns[k - 1], columns[k]
        middle = left + PITCH // 2
        if kind == "D":
            a, b = sheet.diode(ref, value, middle, Y_TOP)
        else:
            a, b = sheet.resistor(ref, value, middle, Y_TOP, lying=True)
        sheet.wire((left, Y_TOP), a)
        sheet.wire(b, (right, Y_TOP))
        sheet.wire((left, Y_BOT), (right, Y_BOT))
    for x in columns[1:-1]:
        sheet.junction((x, Y_TOP))
        sheet.junction((x, Y_BOT))


def build() -> List[Tuple[str, str]]:
    out = []
    specs = [
        ("loops_two", "Two-loop network", lambda s: ladder(s, 2)),
        ("loops_three", "Three-loop ladder", lambda s: ladder(s, 3)),
        ("loops_grid", "Four loops in a 2 x 2 grid", grid),
        ("loops_seven", "Seven-loop ladder", lambda s: ladder(s, 7)),
        ("loops_crossing", "Two-loop network with a crossing wire",
         _crossing),
        ("loops_scattered_ground", "Two loops closed by scattered grounds",
         lambda s: ladder(s, 2, bottom="scattered")),
        ("loops_two_sources", "Two sources, two loops turning opposite ways",
         lambda s: network(s, [("V", "V1", "10V"), ("R", "R2", "1k"),
                               ("V", "V2", "10V")],
                           [("R", "R1", "1k"), ("R", "R3", "1k")])),
        ("loops_middle_source", "One source in the shared branch",
         lambda s: network(s, [("R", "R1", "1k"), ("V", "V1", "9V"),
                               ("R", "R4", "2k2")],
                           [("R", "R2", "1k"), ("R", "R3", "1k")])),
        ("loops_current_source", "A current source driving two loops",
         lambda s: network(s, [("I", "I1", "2mA"), ("R", "R2", "1k"),
                               ("R", "R4", "2k2")],
                           [("R", "R1", "470"), ("R", "R3", "1k")])),
        ("loops_undecidable", "Opposing sources and a diode",
         lambda s: network(s, [("V", "V1", "10V"), ("R", "R2", "1k"),
                               ("R", "R4", "1k"), ("V", "V2", "10V")],
                           [("R", "R1", "1k"), ("D", "D1", "1N4148"),
                            ("R", "R3", "1k")])),
    ]
    for serial, (name, title, draw) in enumerate(specs, start=1):
        sheet = _Sheet(name, serial)
        draw(sheet)
        out.append((f"{name}.kicad_sch", sheet.text(title)))
    return out


def _crossing(sheet: _Sheet) -> None:
    ladder(sheet, 2)
    x = X0 + PITCH
    sheet.wire((x - 635, Y_TOP + 635), (x + 635, Y_TOP + 635))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for name, text in build():
        with open(os.path.join(here, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
