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

The main window walkthrough, top to bottom:

1. **Input** — Click **Browse...** (Alt+B) to pick a `.kicad_sch` file, or
   simply drag and drop one anywhere onto the window. The read-only *Input
   file* field shows the chosen path.
2. **Output Options** —
   - **Generate Alt Text** (Alt+A): write the natural-language description
     to a text file and show it in the results area.
   - **Generate Image** (Alt+M): export the CircuiTikZ rendering.
   - **Export format** (Alt+E): *PDF*, *SVG*, *PNG* or *All*. Enabled only
     while *Generate Image* is checked.
   - **Show junction dots** (Alt+J): draw the filled dot where three or more
     wires meet, as KiCad does. On by default; uncheck for a cleaner drawing.
     Wiring and alt text are unchanged either way.
3. **Output Folder** — defaults to an `accessible` folder next to the input
   file; click **Choose...** (Alt+H) to change it.
4. **Generate** (Alt+G) — runs the conversion in the background. The button
   is enabled once an input file is selected and at least one output option
   is checked; the controls are disabled (never frozen) while it runs.
5. **Progress** — a read-only log of pipeline stages, warnings, errors and
   the paths of produced files. Every line is mirrored to the status bar so
   screen readers announce it.
6. **Results** — a **Conversion summary** (Alt+V), the generated alt text in
   a read-only, screen-reader friendly text area, a scaled preview of the PNG
   (when one was rendered) whose accessible description *is* the alt text,
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

The GUI always writes the **detailed** description — there is no reason to
hand a blind reader a shorter one. The CLI keeps `-d/--detail` for scripting
when a terser summary is wanted.

Your options (checkboxes, format, junction dots, output folder) are
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
```

Giving both `--no-alt-text` and `--no-image` produces no files but still
parses the schematic and checks its connectivity — a quick validation pass.

Generated files are named after the input file: `<stem>_alt_text.txt`,
`<stem>.tex`, `<stem>.pdf`, `<stem>.svg`, `<stem>.png`.

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

And a snippet of the generated `rc_divider.tex`:

```latex
% Generated by SchemAccess from rc_divider.kicad_sch
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
python -m pytest                        # everything (~325 tests)
python -m pytest -v                     # one line per test, pass/fail
python -m pytest -m "not slow"          # skip the LaTeX compilation tests
```

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

## License

MIT — see [LICENSE](LICENSE).
