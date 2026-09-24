# Test fixture corpus

Sample KiCad 8 schematics (`version 20231120`) for exercising the SchemAccess
parser (`kicad_parser`), net builder (`netbuilder`) and the generators built on
top of them. Every valid fixture embeds full `lib_symbols` definitions, places
wires exactly on absolute pin positions, and adds junctions wherever three or
more things meet at a point. `manifest.json` records, for each valid fixture,
the component count (power symbols excluded), net count, whether a ground net
exists, and the number of power symbols — exactly as `build_graph` reports
them.

Pins that are intentionally terminal (a labelled input port, or a lone pin on
a supply rail) carry a `no_connect` marker so the netbuilder's dangling-pin
diagnostic stays quiet; the pins are still electrically attached to their
labels/rails.

| Fixture | Circuit | What it exercises |
| --- | --- | --- |
| `rc_divider.kicad_sch` | V1 5V, series R1, R2 parallel C1 to ground | Reference fixture: sources, parallel branches, junctions, multiple GND symbols |
| `voltage_divider.kicad_sch` | +5V rail, R1 10k / R2 10k, VOUT midpoint | `power:+5V` rail naming (POWER net kind), local label on a wire interior |
| `rc_filter.kicad_sch` | IN — R1 1k — OUT, C1 100nF shunt to GND | Global labels naming nets, T-junction, no_connect port markers |
| `rlc_series.kicad_sch` | VSIN — R1 100 — L1 10mH — C1 1uF loop to GND | `Simulation_SPICE:VSIN` (AC source), `Device:L` inductor, series loop, GND pin joining two collinear wires |
| `led_battery.kicad_sch` | BT1 9V — R1 470 — LED D1 — back to BT1 | `Device:Battery_Cell`, `Device:LED` polarity (pin 1 K, pin 2 A), 180-degree rotation, a circuit with **no** ground net |
| `opamp_inverting.kicad_sch` | VIN — R1 10k — U1 inverting input, R2 100k feedback, + to GND, VOUT | 3-pin op-amp symbol, feedback topology, junctions at inverting input and output nodes |
| `wheatstone.kicad_sch` | V1 10V driving R1..R4 1k bridge, midpoints A/B | Multi-branch nets (3 pins each on top/ground), local labels A and B, several junctions |
| `logic_gates.kicad_sch` | Y = (A AND B) OR C via 74LS08 + 74LS32 | Gate classification from lib_id, input/output pin etypes, gate-to-gate wiring, four labelled ports |
| `hier_parent.kicad_sch` | V1 5V into sheet pin SIG of the child sheet | `(sheet ...)` parsing, Sheetname/Sheetfile properties, sheet-pin ↔ hierarchical-label joining, hierarchy flattening |
| `hier_child.kicad_sch` | SIG — R1 1k — GND | Hierarchical label; also valid standalone (counts in manifest are for standalone parsing) |
| `malformed.kicad_sch` | Truncated file, unbalanced parentheses | Must raise `KiCadParseError` (S-expression error path) |
| `not_a_schematic.kicad_sch` | Valid s-expr, but top tag `kicad_pcb` | Must raise `KiCadParseError` (wrong document type path) |
| `big_200.kicad_sch` | 100-stage RC ladder: 200 components, 100 GND symbols, 102 nets | Performance / scaling; regenerate with `python gen_big.py` (deterministic, byte-identical output) |
| `gen_big.py` | — | Generator script for `big_200.kicad_sch` |
| `earth_blank_value.kicad_sch` | V1 5V across R1 100k, bottom wire to `power:Earth` | A user's KiCad 9 file, verbatim: the Earth's Value was emptied (KiCad writes `~`), which once named the net `~`, drew a vcc arrow and split the SPICE ground; also a text note |
| `power_symbols.kicad_sch` | 20 resistors hanging off power symbols | Every ground in KiCad 10's power library (symbol definitions copied from KiCad 10.0), emptied Values (`~` and `""`), grounds and supplies turned sideways or hanging down, a blanked +5V joining a named one, and a `lib_name` alias (R99) |
| `loops_two.kicad_sch` | The textbook two-loop network: V1, R1/R3 along the top, R2 and R4 upright | Loop currents (`--loops`): i1 through V1, R1, R2 and i2 through R2, R3, R4, both clockwise, sharing R2 |
| `loops_three.kicad_sch` | The same ladder, three windows | Three loop currents, numbered left to right |
| `loops_grid.kicad_sch` | Four windows in a 2 x 2 grid round a four-way junction | Loop numbering row by row (i1, i2 on top; i3, i4 below), a bare branch and a bare rail |
| `loops_seven.kicad_sch` | A seven-window ladder | One loop past the limit: no loop currents drawn, and the report says why |
| `loops_crossing.kicad_sch` | The two-loop network plus a stray wire crossing a branch without a junction | A drawing that is not a flat map of its circuit: no loop currents drawn |
| `loops_scattered_ground.kicad_sch` | The two-loop network closed by GND symbols not in one row | Loops that cannot be traced on the page: none drawn (GND symbols *in* a row, as in `rc_divider`, are read as a rail) |
| `loops_two_sources.kicad_sch` | V1 down the left, V2 down the right, both + on top | A second source turns its loop the other way: i1 clockwise, i2 counterclockwise, i1 + i2 down R2 |
| `loops_middle_source.kicad_sch` | V1 in the shared middle branch | The source's current splits: i1 counterclockwise, i2 clockwise |
| `loops_current_source.kicad_sch` | A 2 mA current source down the left | Loops follow SPICE's current-source direction (+ to - through it): both counterclockwise |
| `loops_undecidable.kicad_sch` | Opposing V1 and V2 with a diode along the middle window | Directions that cannot be worked out: no loops, and the reason names D1 |
| `gen_loops.py` | — | Generator script for the ten `loops_*` fixtures (deterministic, byte-identical output) |

Notes:

- Coordinates are millimetres with the Y axis pointing **down**; symbols sit
  on a 1.27 mm grid (Battery_Cell uses half-grid body centres so its
  ±1.905 mm pins land on-grid).
- `hier_parent.kicad_sch` manifest counts describe the *flattened* design
  (parent + child), because `parse_file` resolves hierarchy by default.
- All uuids are unique within each file and every file is deterministic —
  no timestamps, no randomness.
