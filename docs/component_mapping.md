# Component mapping reference

How a KiCad symbol becomes a typed component and, from there, a CircuiTikZ
element. Two tables in the source code drive this:

- `_LIB_NAME_MAP` / `_PREFIX_MAP` in `src/schemaccess/model.py` classify a
  symbol into a `ComponentType`;
- `_BIPOLE_KEYS`, `_TRANSISTOR_STYLES` and `_GATE_STYLES` in
  `src/schemaccess/circuitikz.py` map a `ComponentType` to a circuitikz
  element.

## Stage 0: pin-name signatures (checked first)

Three-terminal devices are recognised by their pin names, which are
standard across every library, rather than by part number:

| Pin names | Resolved with | ComponentType |
| --- | --- | --- |
| `D`, `G`, `S` | `_fet_kind()`: "jfet" in the symbol's description/keywords, or a part number in `_JFET_PREFIXES` (BF24x, J1xx, 2N54xx, MMBF…) → JFET; otherwise MOSFET. "p-channel" in the hints selects the P variant | NJFET / PJFET / NMOS / PMOS |
| `B`, `C`, `E` | "pnp" or "npn" in the symbol name, description or keywords | TRANSISTOR_PNP / TRANSISTOR_NPN |

This matters because KiCad keeps JFETs and MOSFETs in the same
`Transistor_FET` library with identical `D`/`G`/`S` pins, and every
bipolar and FET part shares the `Q` reference prefix — so neither the
library nor the designator can tell them apart on its own.

## Stage 1: KiCad `lib_id` → `ComponentType`

`model.classify()` takes the symbol name — the part of the `lib_id` after
the colon (`Device:R_Small` → `r_small`), lower-cased — and walks the table
below **in order; the first match wins**. Two match rules exist:

- **exact** — the name must equal the pattern;
- **prefix** — the name may also merely *start with* the pattern. This rule
  applies when the pattern ends in `_` or is longer than two characters.

| # | Pattern | Rule | ComponentType | Matches e.g. |
| --- | --- | --- | --- | --- |
| 1 | `r_potentiometer` | prefix | POTENTIOMETER | `Device:R_Potentiometer` |
| 2 | `r_variable` | prefix | POTENTIOMETER | `Device:R_Variable` |
| 3 | `r_pack` | prefix | IC | `Device:R_Pack04` |
| 4 | `r_` | prefix | RESISTOR | `Device:R_Small`, `Device:R_US` |
| 5 | `r` | exact | RESISTOR | `Device:R` |
| 6 | `c_polarized` | prefix | CAPACITOR_POLARIZED | `Device:C_Polarized` |
| 7 | `cp` | exact | CAPACITOR_POLARIZED | `Device:CP` |
| 8 | `c_` | prefix | CAPACITOR | `Device:C_Small` |
| 9 | `c` | exact | CAPACITOR | `Device:C` |
| 10 | `l_` | prefix | INDUCTOR | `Device:L_Small` |
| 11 | `l` | exact | INDUCTOR | `Device:L` |
| 12 | `led` | prefix | LED | `Device:LED`, `Device:LED_RGB` |
| 13 | `d_zener` | prefix | ZENER | `Device:D_Zener` |
| 14 | `d_schottky` | prefix | DIODE | `Device:D_Schottky` |
| 15 | `d_` | prefix | DIODE | `Device:D_Small` |
| 16 | `d` | exact | DIODE | `Device:D` |
| 17 | `battery` | prefix | BATTERY | `Device:Battery`, `Device:Battery_Cell` |
| 18 | `vdc` | prefix | VOLTAGE_SOURCE | `Simulation_SPICE:VDC` |
| 19 | `vsource` | prefix | VOLTAGE_SOURCE | `pspice:VSOURCE` |
| 20 | `vac` | prefix | AC_SOURCE | `Simulation_SPICE:VAC` |
| 21 | `vsin` | prefix | AC_SOURCE | `Simulation_SPICE:VSIN` |
| 22 | `idc` | prefix | CURRENT_SOURCE | `Simulation_SPICE:IDC` |
| 23 | `isource` | prefix | CURRENT_SOURCE | `pspice:ISOURCE` |
| 24 | `q_npn` | prefix | TRANSISTOR_NPN | `Device:Q_NPN_BCE` |
| 25 | `q_pnp` | prefix | TRANSISTOR_PNP | `Device:Q_PNP_BCE` |
| 26 | `q_nmos` | prefix | NMOS | `Device:Q_NMOS_GDS` |
| 27 | `q_pmos` | prefix | PMOS | `Device:Q_PMOS_GDS` |
| 28 | `bc547` | prefix | TRANSISTOR_NPN | `Transistor_BJT:BC547` |
| 29 | `2n2222` | prefix | TRANSISTOR_NPN | `Transistor_BJT:2N2222` |
| 30 | `2n7002` | prefix | NMOS | `Transistor_FET:2N7002` |
| 31 | `opamp` | prefix | OPAMP | `Simulation_SPICE:OPAMP` |
| 32 | `lm358` | prefix | OPAMP | `Amplifier_Operational:LM358` |
| 33 | `lm741` | prefix | OPAMP | `Amplifier_Operational:LM741` |
| 34 | `tl07` | prefix | OPAMP | `Amplifier_Operational:TL071`, `TL072` |
| 35 | `sw_push` | prefix | PUSHBUTTON | `Switch:SW_Push` |
| 36 | `sw_` | prefix | SWITCH | `Switch:SW_SPST` |
| 37 | `sw` | exact | SWITCH | `Switch:SW` |
| 38 | `fuse` | prefix | FUSE | `Device:Fuse` |
| 39 | `crystal` | prefix | CRYSTAL | `Device:Crystal` |
| 40 | `transformer` | prefix | TRANSFORMER | `Device:Transformer_1P_1S` |
| 41 | `conn` | prefix | CONNECTOR | `Connector:Conn_01x02_Pin` |
| 42 | `and` | prefix | AND_GATE | `AND2` (any name starting `and`) |
| 43 | `nand` | prefix | NAND_GATE | `NAND2` |
| 44 | `nor` | prefix | NOR_GATE | `NOR2` |
| 45 | `xnor` | prefix | XNOR_GATE | `XNOR2` |
| 46 | `xor` | prefix | XOR_GATE | `XOR2` |
| 47 | `or` | exact | OR_GATE | `OR` |
| 48 | `not` | prefix | NOT_GATE | `NOT`, `NOT1` |
| 49 | `inverter` | prefix | NOT_GATE | `Inverter` |
| 50 | `buffer` | prefix | BUFFER | `Buffer` |
| 51 | `74ls00` | prefix | NAND_GATE | `74xx:74LS00` |
| 52 | `74ls02` | prefix | NOR_GATE | `74xx:74LS02` |
| 53 | `74ls04` | prefix | NOT_GATE | `74xx:74LS04` |
| 54 | `74ls08` | prefix | AND_GATE | `74xx:74LS08` |
| 55 | `74ls32` | prefix | OR_GATE | `74xx:74LS32` |
| 56 | `74ls86` | prefix | XOR_GATE | `74xx:74LS86` |

### Fallback: reference-designator prefix

When no library-name pattern matches, `classify()` falls back to the
non-digit prefix of the reference designator (`_PREFIX_MAP`):

| Ref prefix | ComponentType |
| --- | --- |
| `R` | RESISTOR |
| `C` | CAPACITOR |
| `L` | INDUCTOR |
| `D` | DIODE |
| `V` | VOLTAGE_SOURCE |
| `I` | CURRENT_SOURCE |
| `BT` | BATTERY |
| `Q` | TRANSISTOR_NPN |
| `U` | IC (or OPAMP when the value contains `amp`) |
| `SW` | SWITCH |
| `S` | SWITCH |
| `F` | FUSE |
| `J` | CONNECTOR |
| `P` | CONNECTOR |
| `Y` | CRYSTAL |
| `T` | TRANSFORMER |
| `RV` | POTENTIOMETER |

Anything still unmatched becomes `ComponentType.UNKNOWN` (alt-text word:
"component").

Power symbols (`power:GND`, `power:+5V`, ... — any symbol whose library
definition is a power symbol or whose reference starts with `#`) never
become components; they contribute net names instead (see stage 2's power
table).

## Stage 2: `ComponentType` → CircuiTikZ element

### Two-terminal components (bipoles)

Components of these types with exactly two pins are emitted as
`\draw ... to[<key>, l={REF}, a={value}] ...;` (`_BIPOLE_KEYS`). Polarity
conventions (diode anode/cathode, source +/-, polarized-capacitor plate)
were verified by compiling against circuitikz 1.6+ and inspecting the
rendered output.

| ComponentType | Alt-text word | circuitikz bipole key |
| --- | --- | --- |
| RESISTOR | resistor | `R` |
| POTENTIOMETER | potentiometer | `pR` (a 3-pin pot draws the track pin1–pin3 plus a wiper lead to pin 2) |
| CAPACITOR | capacitor | `C` |
| CAPACITOR_POLARIZED | polarized capacitor | `cC` |
| INDUCTOR | inductor | `L` |
| DIODE | diode | `D` |
| LED | LED | `leD` |
| ZENER | Zener diode | `zD` |
| VOLTAGE_SOURCE | voltage source | `V` |
| CURRENT_SOURCE | current source | `I` |
| BATTERY | battery | `battery1` |
| AC_SOURCE | AC voltage source | `sV` |
| SWITCH | switch | `nos` (normally-open switch) |
| PUSHBUTTON | push button | `nopb` (normally-open push button) |
| FUSE | fuse | `fuse` |
| CRYSTAL | crystal | `generic` |

### Transistors (3+ pins)

Emitted as a `\node[<style>]` with leads drawn from the node anchors to the
true pin positions (`_TRANSISTOR_STYLES`). Pins are matched to anchors by
the first letter of the pin name; unmatched pins get a plain lead and a
warning.

| ComponentType | Alt-text word | circuitikz node style | Anchors |
| --- | --- | --- | --- |
| TRANSISTOR_NPN | NPN transistor | `npn` | B, C, E |
| TRANSISTOR_PNP | PNP transistor | `pnp` | B, C, E |
| NMOS | N-channel MOSFET | `nmos` | G, D, S |
| PMOS | P-channel MOSFET | `pmos` | G, D, S |

### Logic gates (2+ pins)

Emitted as a `\node[<style>]` with leads from `in 1`/`in 2`/`out` anchors
(`_GATE_STYLES`). Inputs and outputs are identified by pin electrical type
(with a positional fallback); a gate whose pin pattern is not recognised —
wrong number of inputs or outputs — falls back to the labelled box, with a
warning.

| ComponentType | Alt-text word | circuitikz node style |
| --- | --- | --- |
| AND_GATE | AND gate | `and port` |
| OR_GATE | OR gate | `or port` |
| NOT_GATE | NOT gate (inverter) | `not port` |
| NAND_GATE | NAND gate | `nand port` |
| NOR_GATE | NOR gate | `nor port` |
| XOR_GATE | XOR gate | `xor port` |
| XNOR_GATE | XNOR gate | `xnor port` |
| BUFFER | buffer | `buffer port` |

### Op-amps (3+ pins)

| ComponentType | Alt-text word | circuitikz node style |
| --- | --- | --- |
| OPAMP | operational amplifier | `op amp` |

Pins are matched by name to the `-`, `+`, `out`, `up` (V+) and `down` (V-)
anchors; a pin with no anchor gets a plain lead and a warning.

### Everything else: the labelled-box fallback

| ComponentType | Alt-text word | Rendering |
| --- | --- | --- |
| IC | integrated circuit | labelled rectangle |
| CONNECTOR | connector | labelled rectangle |
| TRANSFORMER | transformer | labelled rectangle |
| UNKNOWN | component | labelled rectangle |

The box is drawn as a rectangle with the reference (and value, when
meaningful) above it; every pin keeps a stub landing on its **exact**
position from the schematic, with a tiny pin-number label inside the box
edge, so all surrounding wiring stays electrically and visually correct.
The same fallback is used for any component with fewer than two connected
pins, and for gates/op-amps whose pins could not be matched.

### Power symbols

Power symbols are not components; the CircuiTikZ generator draws them as
flags at their pin position:

| Symbol | Rendering |
| --- | --- |
| Ground family (`GND`, `AGND`, `DGND`, `GNDREF`, `GNDPWR`, `EARTH`, `0`, `VSS`, ...) | `node[ground]` |
| Positive rail (e.g. `+5V`, `VCC`, `VDD`) | `node[vcc]` with the rail name |
| Negative rail (name contains `VEE`, `VSS`, `V-`, `-V`, or starts with `-`) | `node[vee]` with the rail name |
| ERC power flags (`#FLG...`) | not drawn (no graphic meaning) |

*(`ComponentType.POWER_FLAG` exists in the enum for completeness but is
never produced by `classify()`; power symbols are filtered out before
classification.)*
