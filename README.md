# SchemAccess

## Installation

SchemAccess itself is pure Python (standard library only). The optional GUI
uses PySide6, and rendering images requires a LaTeX toolchain on your `PATH`.

### Windows

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) and make
   sure `python` is on your `PATH`.
2. Install SchemAccess from the project folder:

   ```
   pip install .
   ```

   To include the desktop GUI (PySide6):

   ```
   pip install .[gui]
   ```

3. Install [MiKTeX](https://miktex.org). It provides `pdflatex` (which can
   auto-install the `circuitikz` package on first use) plus `pdftocairo` and
   `dvisvgm` for SVG and PNG export.

### Linux / Unix

1. Python 3.10+ (usually preinstalled).
2. Install SchemAccess:

   ```
   pip install .          # core + CLI
   pip install .[gui]     # also install the PySide6 GUI
   ```

3. Install TeX Live and Poppler. On Debian/Ubuntu:

   ```
   sudo apt install texlive texlive-pictures poppler-utils
   ```

   On Fedora:

   ```
   sudo dnf install texlive texlive-pictures poppler-utils
   ```

   `texlive-pictures` supplies the `circuitikz` package; `poppler-utils`
   supplies `pdftocairo`/`pdftoppm` for SVG and PNG export.

Without a LaTeX toolchain SchemAccess still works: it produces the alt text
and the `.tex` source, and tells you how to install the missing tools.

## Usage

### GUI

Launch the desktop app:

```
schemaccess-gui
```

(or `python -m schemaccess.gui.app` if the scripts directory is not on your
`PATH`).

The main window has two columns: settings and progress on the left,
results on the right. In tab order:

1. **Input** — Click **Browse...** (Alt+B) to pick a `.kicad_sch` file, or
   simply drag and drop one anywhere onto the window. The read-only *Input
   file* field shows the chosen path.
2. **Output Options** — the alt text and the CircuiTikZ drawing are both
   always written; these settings say how.
   - **Export format** (Alt+E): *PDF*, *SVG*, *PNG* or *All*.
   - **Show junction dots** (Alt+J): draw the filled dot where three or more
     wires meet, as KiCad does. On by default; uncheck for a cleaner drawing.
     Wiring and alt text are unchanged either way.
   - **Show loop currents** (Alt+L): for teaching mesh analysis, draw each
     loop current (I1, I2, ...) and describe it in the alt text. Off by
     default; see [Loop currents](#loop-currents).
   - **Description detail** (Alt+D): *Short*, *Standard* or *Detailed*
     (the default) alt text. *Detailed* also lists every connection twice,
     by component and by node — see [Example output](#example-output).
   - **Netlists** (Alt+N): also write a SPICE deck, a KiCad netlist, a
     readable net table, a CSV, or all four. *None* by default.
3. **Output Folder** — defaults to an `accessible` folder next to the input
   file; click **Choose...** (Alt+H) to change it.
4. **Generate** (Alt+G) — runs the conversion in the background. The button
   is enabled once an input file is selected and at least one output option
   is checked; the controls are disabled (never frozen) while it runs.
5. **Progress** — a read-only log of pipeline stages, warnings, errors and
   the paths of produced files. Every line is mirrored to the status bar so
   screen readers announce it.
6. **Results** — a **Conversion summary** (Alt+V), the generated alt text in
   a read-only, screen-reader friendly text area, a preview of the PNG
   (when one was rendered), scaled to fit so the whole drawing is always
   visible, whose accessible description *is* the alt text,
   and an **Open Output Folder** button (Alt+O).

The conversion summary looks like this:

```
25 components in the KiCad schematic (28 symbols placed).
11 nodes (46 nets in total).
25 of 25 components converted to CircuiTikZ symbols.
25 of 25 components described in the alt text.
Converted in 161 ms (read 141 ms, drawing 10 ms, description 10 ms).
LaTeX rendering took 7119 ms.
```

Anything that did not convert is named explicitly, so a symbol never fails
silently. Timings cover the translation itself; running LaTeX is an external
tool and is reported separately.

Your options (checkboxes, format, detail, netlists, output folder) are
remembered between sessions.

### CLI

```
schemaccess INPUT.kicad_sch [options]
```

(or `python -m schemaccess.cli ...`). Options:

| Option | Meaning |
| --- | --- |
| `-o DIR`, `--output-dir DIR` | Folder for generated files (default: `<input folder>/accessible`) |
| `--no-alt-text` | Skip the natural-language alt text |
| `--no-image` | Skip CircuiTikZ/LaTeX and rendered images |
| `-f`, `--format {pdf,svg,png,all}` | Image format(s) to render (default: `all`) |
| `--check` | Report what the schematic converts to **without writing any files**; exits 1 if a component did not convert or was left out of the description |
| `--no-junction-dots` | Omit the connection dots drawn where wires meet (included by default, as KiCad draws them) |
| `--loops` | Draw and describe the loop currents I1, I2, ... for teaching mesh analysis (see [Loop currents](#loop-currents)) |
| `--units` | Write units after the values on the drawing (`1` → `1 H`, `22n` → `22 nF`, `100k` → `100 kΩ`) |
| `--netlist FORMATS` | Also write netlists: a comma-separated list of `spice`, `kicad`, `text`, `csv`, or `all` |
| `--compare-net FILE` | Check the conversion against a `.net` netlist exported from KiCad (see [Validating against KiCad](#validating-against-kicad)); writes no files, exits 1 on a disagreement |
| `--compare-cir FILE` | The same check against a `.cir` SPICE deck exported from KiCad; may be combined with `--compare-net` |
| `--generated-net FILE` | Compare this already-written `.net` against `--compare-net` instead of converting the schematic again |
| `--generated-cir FILE` | Compare this already-written `.cir` against `--compare-cir` instead of converting the schematic again |
| `--svg-preview` | Also write `<stem>_preview.svg`, drawn without LaTeX |
| `--pdf-preview` | Also write `<stem>_preview.pdf`, the same drawing as a vector PDF |
| `--print-alt` | Also print the generated alt text to stdout |
| `-q`, `--quiet` | Suppress progress and `wrote:` lines (warnings/errors still go to stderr) |
| `--version` | Show the version and exit |

Exit codes: `0` success, `1` the conversion ran but produced errors, `2` bad
command-line arguments.

Examples:

```
schemaccess board.kicad_sch
schemaccess board.kicad_sch -o out --format svg --detail detailed
schemaccess board.kicad_sch --no-image --print-alt --quiet
schemaccess board.kicad_sch --no-junction-dots --format pdf
schemaccess board.kicad_sch --netlist all --svg-preview --pdf-preview
schemaccess board.kicad_sch --netlist spice,csv --no-image
schemaccess board.kicad_sch --compare-net board.net --compare-cir board.cir
```

Giving both `--no-alt-text` and `--no-image` produces no files but still
parses the schematic and checks its connectivity — a quick validation pass.

Generated files are named after the input file: `<stem>_alt_text.txt`,
`<stem>.tex`, `<stem>.pdf`, `<stem>.svg`, `<stem>.png`, and — when asked
for — `<stem>.cir`, `<stem>.net`, `<stem>_netlist.txt`,
`<stem>_netlist.csv`, `<stem>_preview.svg` and
`<stem>_preview.pdf`.

### Web

SchemAccess also runs in a web browser, with the same options and outputs
as the desktop program:

```
python web/build.py
python -m http.server 8765 --directory web
```

Open <http://localhost:8765> and upload a `.kicad_sch` file (or a `.zip` of
a hierarchical project). See [web/README.md](web/README.md) for deploying
the site.

## Loop currents

For teaching mesh analysis, `--loops` (the **Show loop currents** option in
the desktop program and on the web page) marks every window of the circuit
with its loop current -- I1, I2, ... -- as a circular arrow, the way a
textbook figure does, and the alt text says which parts each loop runs
through and what a shared part carries (I1 minus I2, or I1 plus I2).

Each arrow is drawn at the centre of its window, where a person would put
it, and **every arrow in a circuit is the same size** — one circle drawn
throughout, as on a blackboard. The size follows the wiring: the largest
that clears the wires and part bodies of every window in that circuit.
Reference and value text is deliberately not counted, because in a small
window it is the labels rather than the wiring that decide how much room
is left, and sizing each arrow around its own nearest label made
neighbouring loops visibly different. The arrows are drawn *beneath* the
labels, so where one crosses a value the text still reads over it.

Each loop turns the way its current really flows, so no loop current comes
out negative. When the circuit can be worked out (resistors and DC sources
with numeric values; capacitors open and inductors shorted, as in DC) the
mesh equations are solved to decide it; otherwise each loop follows the
source in it -- current leaves a voltage source's + terminal, and follows a
current source's arrow. So one source's loop runs one way, and a second
source in another branch can send its loop the other way.

**Every circuit on the sheet is treated as its own problem.** A page of
worked examples holds several circuits side by side, so each one gets its
loops counted, solved and numbered separately, starting again at I1 — and
a circuit that cannot be meshed costs only itself, not the whole page.
Two circuits that meet at nothing but a shared ground are two circuits:
no loop can be traced through a single node.

**There is no limit on how many loops a circuit may have.** A ladder of
twenty windows is labelled I1 to I20; every arrow in a circuit is drawn
the same size, chosen so the tightest window still fits, and the arrows
are drawn beneath the reference and value text so a label always reads
over the top of one.

What the loops do need is a circuit they can be traced on: a flat circuit
of two-terminal parts, drawn with wires (ground symbols in one row count
as a wire). A circuit that is anything else -- an op amp or transistor,
wires crossing without a junction, parts joined by net labels, no source,
or sources whose directions cannot be told apart -- is converted as usual
without them, and the report says which circuit was left out and why.

## Netlists

`--netlist` writes the circuit's connectivity in four shapes. All four
come from the same electrical graph the alt text is written from, so they
always agree with each other.

| Format | File | What it is |
| --- | --- | --- |
| `spice` | `<stem>.cir` | An ngspice/LTspice deck. Ground is node `0`; every other net keeps its name. |
| `kicad` | `<stem>.net` | KiCad's own S-expression netlist, the format `Tools > Generate Netlist` writes. |
| `text` | `<stem>_netlist.txt` | A component and net table written to be *read*, by a person or a screen reader. |
| `csv` | `<stem>_netlist.csv` | One row per pin: net, reference, component, value, pin number, pin name, pin type. |

The SPICE deck is meant to run. Values are converted to SPICE magnitudes
(`4k7` → `4.7k`, `22nF` → `22n`, `1MEG` → `1Meg`), diodes are written
anode-first, sources positive-terminal-first, and MOSFET bulk is tied to
source. Devices SPICE cannot infer from a schematic alone are **never
guessed at**: a switch, a controlled source or a transformer is written
as a commented-out line with its nodes already filled in, so you uncomment
it and supply the one number the schematic does not carry. Op amps call an
ideal infinite-gain subcircuit included at the end of the deck. Every one
of those decisions is reported — on the command line as a warning, in the
web page as a note.

```
* rc_divider.kicad_sch - netlist generated by SchemAccess
* 4 components, 3 nets

V1 N1 0 DC 5
C1 N2 0 22n
R1 N1 N2 20
R2 N2 0 100

* Add an analysis, e.g.  .op   .tran 1u 5m   .ac dec 20 1 1Meg
.end
```

## Validating against KiCad

A conversion is only trustworthy if SchemAccess read the schematic the way
KiCad does. `--compare-net` and `--compare-cir` check exactly that against
KiCad's own netlist for the same file, so KiCad becomes an independent
second opinion rather than something you have to take on trust.

Converting never needs this: a `.kicad_sch` file is still the only input,
and the GUI is unchanged. The check is a separate, optional run.

1. Convert as usual.
2. Export the reference from KiCad — `Tools > Generate Netlist` in
   Eeschema, or on the command line:

```
kicad-cli sch export netlist --format kicadsexpr -o board.net board.kicad_sch
```

```
kicad-cli sch export netlist --format spice -o board.cir board.kicad_sch
```

3. Point SchemAccess at them. Either file on its own is fine, or both:

```
schemaccess board.kicad_sch --compare-net board.net --compare-cir board.cir
```

The run writes nothing, prints `PASS` or `FAIL` per file, and exits 1 if
anything disagreed:

```
PASS  board.net  (KiCad reference vs SchemAccess)
  checked:
    3 component(s) with matching references and values
    3 net(s) joining the same pins on both sides
    ground on R2 pin 1, V1 pin 2
```

### Comparing netlists already on disk

By default the schematic is converted again, in memory, to produce the
netlist to compare — the `.kicad_sch` file is the input, and the reference
is the only thing read from disk. To compare a netlist that already exists
instead — one an earlier run wrote to `accessible/`, or one from the web
front end or another machine — name it with `--generated-net` /
`--generated-cir`:

```bash
schemaccess --compare-net kicad/board.net --generated-net accessible/board.net
```

When every comparison has both of its files, nothing needs converting and
the schematic can be left off the command line entirely. Mixing the two
modes is fine: give `--generated-cir` and not `--generated-net`, and the
SPICE deck is read from disk while the `.net` is regenerated from the
schematic (which is then still required).

Both `.net` files record the schematic they were written from, so
comparing two that came from *different* schematics is reported as a
warning either way — with or without a schematic on the command line.

**Net names are never compared.** KiCad calls a net `Net-(R1-Pad1)` or
`/VOUT` where SchemAccess calls it `N1` or `VOUT`, and a SPICE ground node
is `GND` on one side and `0` on the other — none of which is electrical.
Two nets match when they join **the same component pins**, whatever either
tool calls them, and two SPICE decks match when zipping the node lists of
every shared device gives a one-to-one mapping between their node names.

What is compared: the components and their values, which pins share a net,
which net is ground, and each SPICE device's node count, node order and
value. Differences in formatting, ordering and naming are ignored.

Anything that *could not* be compared is listed separately, with the
reason, and does not count as agreement — for instance KiCad's SPICE
writer emits a bare `D1 __D1` for any symbol it cannot map to an ngspice
primitive, and writes `_NO_NET_` for every net it could not name, so those
positions carry no information to check against.

The same comparison runs over the whole fixture corpus in the test suite;
see [docs/TESTING.md](docs/TESTING.md).

## Drawing without LaTeX

`--svg-preview` and `--pdf-preview` write `<stem>_preview.svg` and
`<stem>_preview.pdf`: the circuit drawn directly from the schematic's
geometry, with no LaTeX involved. They exist so there is a picture on a
machine with no TeX toolchain, and so the web page can show something
instantly and hand over a printable file.

Both come from the same drawing code — `svgpreview.draw()` takes a canvas,
and `pdfwriter` supplies a second one — so a symbol fixed in one is fixed
in both. The PDF is written by hand against the PDF imaging model (paths,
transforms, the base-14 fonts) in the standard library alone; it is real
vector output that scales and prints, not a rasterised image.

These are a *preview*, not a second renderer: topology, positions and
labels always match the CircuiTikZ output because both work from the same
graph, but the symbol artwork is drawn by SchemAccess rather than by
circuitikz, so it will not match a `pdflatex` rendering stroke for stroke.
The `.tex` output remains the authoritative drawing.

## Supported components

These KiCad symbols convert to a real CircuiTikZ symbol. Anything else is
drawn as a labelled box with its pins in the correct places — still wired
correctly, and still fully described in the alt text — and is reported in the
conversion summary so you know it happened.

| Component | CircuiTikZ element |
| --- | --- |
| Resistor | `to[R]` |
| Potentiometer | `to[pR]` (+ wiper lead) |
| Capacitor | `to[C]` |
| Polarized capacitor | `to[cC]` |
| Inductor | `to[L]` |
| Diode | `to[D]` |
| LED | `to[leD]` |
| Zener diode | `to[zD]` |
| Voltage source (DC) | `to[V]` |
| AC / sinusoidal source | `to[sV]` |
| Current source | `to[I]` |
| Battery | `to[battery1]` |
| Controlled voltage source | `to[cvsource]` |
| Controlled current source | `to[cisource]` |
| Switch | `to[nos]` |
| Push button | `to[nopb]` |
| Fuse | `to[fuse]` |
| Crystal | `to[generic]` |
| NPN transistor | `node[npn]` |
| PNP transistor | `node[pnp]` |
| N-channel MOSFET | `node[nmos]` |
| P-channel MOSFET | `node[pmos]` |
| N-channel JFET | drawn from KiCad's own geometry |
| P-channel JFET | drawn from KiCad's own geometry |
| Operational amplifier | `node[op amp]` |
| Transformer (2 windings) | `node[transformer core]` |
| AND / OR / NOT / NAND / NOR / XOR / XNOR / buffer | `node[... port]` |
| Ground and power rails | `node[ground]`, `node[vcc]`, `node[vee]` |

How a symbol is recognised, in order:

1. **Pin-name signature** — `D`/`G`/`S` means a FET, `B`/`C`/`E` a bipolar.
   The family (JFET vs MOSFET, NPN vs PNP) comes from the symbol's
   description and keywords, plus a small JFET part-number list, because
   KiCad keeps both in one library with identical pins.
2. **Symbol name** — anything calling itself a source is classified from its
   own name (`Independent_Current_Source`, `Dependent_Voltage_Source`), then
   from its value's unit (`5A` vs `5V`).
3. **Library name** — `Device:R_Small`, `Amplifier_Operational:*`, and so on.
4. **Reference prefix** — `R`, `C`, `L`, `D`, `Q`, `U`, `T`/`TR`, … as a
   last resort.

The **drawn outline follows your schematic**: a source you drew as a diamond
stays a diamond, even when its name says otherwise. Polarity and
winding-phase dots in the symbol artwork are reproduced too.

Custom symbols work as long as their pins are named conventionally — see
[docs/component_mapping.md](docs/component_mapping.md) for the full tables
and how to add a new mapping.

## Example output

Running the CLI on the bundled reference fixture (an RC divider: a 5 V source
feeding R1 into an R2 ‖ C1 branch to ground):

```
python -m schemaccess.cli tests\fixtures\rc_divider.kicad_sch -o demo --print-alt -f pdf
```

```
Reading KiCad schematic...
Parsing components...
Generating connectivity graph...
Creating alt text...
Generating CircuiTikZ...
Rendering PDF...
Done.
wrote: demo\rc_divider_alt_text.txt
wrote: demo\rc_divider.tex
wrote: demo\rc_divider.pdf
```

The generated alt text (`standard` detail):

```
There are 4 elements and 3 nodes in the circuit.
Between ground and node 2, a 100 Ohm resistor labelled R2 is connected in parallel with a 22 nanofarad capacitor labelled C1.
Between ground and node 2, a 5 Volt voltage source labelled V1 is connected in series with a 20 Ohm resistor labelled R1, these elements are connected at node 1.
The positive terminal of the voltage source is connected to node 1 and the negative terminal is connected to ground.
```

`detailed` adds the detected structures and then lists the wiring twice
over — once per component, once per node:

```
Detected structures:
R1 and R2 form a voltage divider between node 1 and ground, with the output tapped at node 2.
R1 and C1 form an RC low-pass filter with input at node 1 and output at node 2.
Connections by component:
V1: pin 1 (+) to node 1; pin 2 (-) to ground.
C1: pin 1 to node 2; pin 2 to ground.
R1: pin 1 to node 1; pin 2 to node 2.
R2: pin 1 to node 2; pin 2 to ground.
Connections by node:
Ground joins V1 pin 2 (-), C1 pin 2, and R2 pin 2.
Node 1 joins V1 pin 1 (+) and R1 pin 1.
Node 2 joins C1 pin 1, R1 pin 2, and R2 pin 1.
```

The same connections read from either side, because they answer different
questions: *what is R1 wired to?* and *what meets at node 2?* The node
view is usually the easier one to follow — it is how a circuit is traced
by hand, and how nodal analysis is set up. It names a terminal by its role
where the part has one, so an op-amp node reads `Node 2 joins R1 pin 2,
R2 pin 1, and the inverting input of U1.`

And a snippet of the generated `rc_divider.tex`:

```latex
% Generated from rc_divider.kicad_sch
% 4 components, 3 nets
\documentclass[border=4pt]{standalone}
\usepackage[RPvoltages]{circuitikz}
\begin{document}
\begin{circuitikz}[american]
% Wires
\draw (0,1.292) -- (0,0);
\draw (0,3.958) -- (0,5.25);
\draw (0,5.25) -- (2.334,5.25);
...
% Two-terminal components
\draw (13.335,3.625) to[C, l={C1}, a={22nF}] (13.335,1.625);
\draw (2.334,5.25) to[R, l={R1}, a={20~$\Omega$}] (4.334,5.25);
\draw (6.668,3.625) to[R, l={R2}, a={100~$\Omega$}] (6.668,1.625);
\draw (0,1.292) to[V, l={V1}, a={5V}] (0,3.958);
% Power symbols
\draw (0,0) node[ground]{};
...
\end{circuitikz}
\end{document}
```

## Troubleshooting

**"No LaTeX toolchain found - wrote the .tex file only."**
`pdflatex` is not on your `PATH`. Install MiKTeX (Windows), TeX Live +
`texlive-pictures` + `poppler-utils` (Linux), or MacTeX + Poppler (macOS),
then re-run. Until then you still get the alt text and the `.tex` source,
which you can compile elsewhere (for example on Overleaf).

**The very first render takes a long time (or seems stuck).**
On a fresh MiKTeX install, the first compile may auto-install the
`circuitikz` package and its dependencies on the fly (SchemAccess passes
`--enable-installer` to MiKTeX's `pdflatex` for exactly this reason). This
one-time download can take a few minutes; later runs are fast. Every external
tool call has a hard 300-second timeout, so a genuinely stuck toolchain
produces an error rather than a hang.

**A component renders as a plain rectangle with numbered pins.**
That is the intended fallback: symbols SchemAccess cannot map to a CircuiTikZ
element (ICs, connectors, unrecognised parts, or gates/op-amps whose pins
don't match the expected pattern) are drawn as a labelled box whose pin stubs
still land on their true positions, so the surrounding wiring stays correct.
A warning names the affected component. See
[docs/component_mapping.md](docs/component_mapping.md) for what is mapped,
and [ARCHITECTURE.md](ARCHITECTURE.md) for how to add new mappings.

**`schemaccess` is not recognised as a command.**
Your Python scripts directory is not on `PATH` (common with `pip install
--user` on Windows). Use `python -m schemaccess.cli` and
`python -m schemaccess.gui.app` instead, or add the scripts directory
(e.g. `%APPDATA%\Python\Python314\Scripts`) to your `PATH`.

**"error: Not a KiCad schematic" / "Malformed S-expression file".**
The input is not a KiCad 6+ `.kicad_sch` file (legacy `.sch` files from
KiCad 5 and earlier are a different format — open and re-save the project in
a current KiCad first), or the file is truncated/corrupted.

**Warnings about unconnected pins.**
`Pin N of X appears unconnected` means a pin landed on a net with no other
pin and no no-connect marker — usually a wire that stops just short of a pin
in the original schematic. The output is still produced; fix the schematic in
KiCad to silence the warning.

## Tests

```
python -m pytest                        # everything (~1900 tests)
python -m pytest -v                     # one line per test, pass/fail
python -m pytest -m "not slow"          # skip the tests that run LaTeX or KiCad
python -m pytest tests/test_kicad_reference.py -vv   # just the KiCad comparison
```

The `slow` tests drive external tools — `pdflatex` and the PDF converters
for the rendering tests, `kicad-cli` for the comparison against KiCad's own
netlists (see [Validating against KiCad](#validating-against-kicad)). They
skip rather than fail when the tool is not installed, so the suite passes on
a bare machine.

Two reports print measured numbers rather than just passing. Use `-s`, which
stops pytest from swallowing their output:

```
python -m pytest tests/test_parser.py -k report -s
```

```
mixed_symbols.kicad_sch
    25 components in KiCad schematic, 11 nodes (46 nets)
    OK   25 converted to CircuiTikZ symbols
    OK   25 described in the alt text
    OK   converted in 12.4 ms (read 11.0, drawing 0.8, description 0.6)
```

Every fixture is listed with `OK`/`FAIL` per line, and any component that
did not convert or was left out of the description is named. The test fails
if anything is missing or over the 5-second budget.

**These run against the frozen schematics in `tests/fixtures/`, not against
whatever file you are working on.** To get the same report for your own
schematic, use `--check`, which converts in memory and writes nothing:

```
python -m schemaccess.cli path\to\your.kicad_sch --check
```

```
20 components in the KiCad schematic (21 symbols placed).
9 nodes (30 nets in total).
20 of 20 components converted to CircuiTikZ symbols.
20 of 20 components described in the alt text.
Converted in 64 ms (read 62 ms, drawing 2 ms, description 1 ms).
All components converted.
```

It exits 1 and names the offenders if anything fails to convert, so it works
in a build script as well as by hand.

```
python -m pytest tests/test_performance.py -s
```

```
PER-1: 200 components translated in 166.3 ms (read 146.4 ms,
       drawing 9.7 ms, description 10.1 ms); budget 5000 ms
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — module layout, data flow, the
  two-layer data model, connectivity rules, determinism guarantees, and how
  to extend the component mappings.
- [docs/component_mapping.md](docs/component_mapping.md) — the full
  KiCad-symbol → component-type → CircuiTikZ-element mapping tables.
- [docs/TESTING.md](docs/TESTING.md) — the requirement/test traceability
  matrix and how to run the test suite.
- [web/README.md](web/README.md) — how the browser build works (Pyodide,
  `build.py`, the driver module) and how to deploy it.

## License

MIT — see [LICENSE](LICENSE).
