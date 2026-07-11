# SchemAccess

**Accessible schematics from KiCad files.**

SchemAccess is a cross-platform desktop tool that makes electronic circuit
schematics accessible to blind and low-vision students. It reads a KiCad
schematic (`.kicad_sch`) and produces:

1. **Natural-language alt text** — a structured, deterministic description of
   the circuit ("a 100 Ohm resistor labelled R2 is connected in parallel with
   a 22 nanofarad capacitor labelled C1 ...") that reads naturally with a
   screen reader, at three levels of detail.
2. **A CircuiTikZ/LaTeX rendering** — clean, publication-quality vector
   output (PDF, SVG and/or PNG) that preserves the original schematic layout,
   for sighted classmates, instructors and printed handouts.

Circuit diagrams are one of the most stubborn accessibility gaps in
electronics education: a screenshot of a schematic carries no information a
screen reader can use. SchemAccess closes that gap by describing the
*electrical structure* of the circuit — components, nodes, series/parallel
relationships, source polarity, detected building blocks such as voltage
dividers, RC filters, Wheatstone bridges, op-amp configurations and logic
gates — instead of its pixels.

## Features

- **Reads real KiCad files**: KiCad 6/7/8/9 `.kicad_sch` schematics,
  including hierarchical sheets (flattened automatically), multi-unit
  symbols, power symbols, junctions, and local/global/hierarchical labels.
- **True electrical connectivity**: nets are built with KiCad semantics
  (junctions, labels, wire-interior connections; crossing wires without a
  junction are *not* connected).
- **Three alt-text detail levels**: `short` (counts and a component list),
  `standard` (parallel groups, series chains, connections, source polarity)
  and `detailed` (everything, plus detected structures, per-component pin
  listings and warnings).
- **Structure detection**: series chains, parallel groups, voltage dividers,
  first-order RC/RL filters (low- and high-pass), Wheatstone bridges,
  inverting / non-inverting / follower op-amp configurations, logic-gate
  networks and power rails.
- **CircuiTikZ export**: a complete standalone LaTeX document that compiles
  with `pdflatex` as-is, then converts to SVG and 300 dpi PNG.
- **Layout preservation**: the rendered drawing keeps the geometry of your
  original schematic.
- **Deterministic output**: identical inputs always produce byte-identical
  text, LaTeX and descriptions — no timestamps, no randomness.
- **Graceful degradation**: unknown or unsupported symbols are drawn as
  labelled boxes with their pins in the correct positions; problems produce
  warnings, never crashes.
- **Accessible GUI**: the desktop app itself is built for screen readers —
  every widget has an accessible name and description, keyboard mnemonics,
  an explicit tab order, and progress is mirrored to the status bar so it is
  announced as it happens.
- **Scriptable CLI** for batch conversion and automation.

## Screenshots

<!-- TODO: add screenshots
![Main window](docs/images/main_window.png)
![Rendered output](docs/images/rc_divider_render.png)
-->

*Screenshots coming soon.*

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
   - **Description detail** (Alt+D): *Short*, *Standard* or *Detailed*.
     Enabled only while *Generate Alt Text* is checked.
   - **Export format** (Alt+E): *PDF*, *SVG*, *PNG* or *All*. Enabled only
     while *Generate Image* is checked.
3. **Output Folder** — defaults to an `accessible` folder next to the input
   file; click **Choose...** (Alt+H) to change it.
4. **Generate** (Alt+G) — runs the conversion in the background. The button
   is enabled once an input file is selected and at least one output option
   is checked; the controls are disabled (never frozen) while it runs.
5. **Progress** — a read-only log of pipeline stages, warnings, errors and
   the paths of produced files. Every line is mirrored to the status bar so
   screen readers announce it.
6. **Results** — the generated alt text in a read-only, screen-reader
   friendly text area; a scaled preview of the PNG (when one was rendered),
   whose accessible description *is* the alt text; and an **Open Output
   Folder** button (Alt+O).

Your options (checkboxes, detail level, format, output folder) are remembered
between sessions.

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
| `-d`, `--detail {short,standard,detailed}` | Alt-text detail level (default: `standard`) |
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
```

Giving both `--no-alt-text` and `--no-image` produces no files but still
parses the schematic and checks its connectivity — a quick validation pass.

Generated files are named after the input file: `<stem>_alt_text.txt`,
`<stem>.tex`, `<stem>.pdf`, `<stem>.svg`, `<stem>.png`.

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
