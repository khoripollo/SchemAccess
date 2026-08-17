# SchemAccess architecture

This document describes how SchemAccess is put together: the modules, the
data flow between them, the two-layer data model, the connectivity rules the
net builder implements, the determinism guarantees, and how to extend the
component mappings.

## Module diagram

```
                          .kicad_sch file
                                 |
                                 v
 +-----------+        +--------------------+
 | sexpr.py  |------->|  kicad_parser.py   |   S-expressions -> SchematicDocument
 +-----------+        +--------------------+   (document layer)
   tokenizer                     |
                                 v
                      +--------------------+
                      |   netbuilder.py    |   SchematicDocument -> CircuitGraph
                      +--------------------+   (circuit layer)
                                 |
              +------------------+------------------+
              |                                     |
              v                                     v
   +--------------------+                +--------------------+
   |    analyzer.py     |                |   circuitikz.py    |
   | structure detection|                |  graph -> LaTeX    |
   +--------------------+                +--------------------+
              |                                     |
              v                                     v
   +--------------------+                +--------------------+
   |    alttext.py      |                |    renderer.py     |
   | graph -> prose     |                | .tex -> PDF/SVG/PNG|
   +--------------------+                +--------------------+
              |                                     |
              +------------------+------------------+
                                 |
                                 v
                      +--------------------+
                      |    pipeline.py     |   single entry point
                      +--------------------+
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
          +------------------+      +------------------+
          |     cli.py       |      |  gui/ (PySide6)  |
          | 'schemaccess'    |      | 'schemaccess-gui'|
          +------------------+      +------------------+

 model.py defines the shared dataclasses used by every stage.
```

## Data flow

```
kicad_parser -> netbuilder -> analyzer -> alttext / circuitikz -> renderer -> pipeline -> gui / cli
```

1. **`sexpr.py`** — a minimal, dependency-free S-expression reader. KiCad 6+
   files are UTF-8 S-expressions; this module parses them into nested Python
   lists (atoms are `str`, `int` or `float`; quoted strings are wrapped in a
   `QuotedString` str subclass so `"100"` stays distinguishable from a bare
   token). Raises `SExprError` with line/column on malformed input.

2. **`kicad_parser.py`** — parses KiCad 6/7/8/9 schematics into a
   `SchematicDocument`: embedded library symbols (with pin definitions),
   placed symbol instances, wires, junctions, labels (local / global /
   hierarchical), no-connect markers and hierarchical sheets. Referenced
   sub-sheets that exist on disk are parsed too and merged (flattened) into
   the returned document. `parse_file(path)` raises `KiCadParseError` only
   when the file cannot be read as a KiCad schematic at all; malformed or
   unsupported constructs *inside* a valid file produce warnings, never
   crashes.

3. **`netbuilder.py`** — `build_graph(doc)` converts the document into a
   `CircuitGraph`: it computes every pin's absolute position, runs a
   union-find over snapped coordinates following KiCad connectivity
   semantics (see below), resolves one name and kind per net, classifies
   each symbol into a `ComponentType`, and merges multi-unit symbols into
   one component per reference. `node_names(graph)` additionally provides
   the human-friendly node names used by the alt text ("ground",
   "the +5V rail", "node 1", "node 3 (VOUT)").

4. **`analyzer.py`** — works purely on the electrical graph (no geometry)
   and detects structures: parallel groups, series chains, voltage dividers,
   first-order RC/RL filters, Wheatstone bridges, the three classic op-amp
   configurations (inverting, non-inverting, follower), logic gates and
   power rails. Returns a `CircuitAnalysis`.

5. **`alttext.py`** — `generate(graph, detail)` turns the graph plus the
   analysis into structured prose at `short` / `standard` / `detailed`
   levels, one sentence per line. Includes a value formatter that turns
   KiCad value strings into words (`4.7k` → "4.7 kiloohm", `22nF` →
   "22 nanofarad", `4k7` → "4.7 kiloohm", `1MEG` → "1 megaohm").

6. **`circuitikz.py`** — `generate(graph)` emits a complete standalone LaTeX
   document (`generate_body` emits just the `circuitikz` environment).
   KiCad millimetre coordinates (Y down) are mapped linearly onto TikZ units
   (Y up) so the drawing preserves the original layout. The factor is
   `SCALE = 0.49 / 2.54`, chosen so KiCad's grid lands on circuitikz's own
   natural proportions: a pin 2.54 mm off centre maps to 0.49 units, exactly
   where circuitikz places an op amp's input anchor. Every symbol therefore
   draws at its natural circuitikz size — the way a hand-written figure
   looks — and op-amp leads meet their pins without scaling the shape.
   Changing `SCALE` resizes every drawing and reintroduces that mismatch, so
   a test pins it to the anchor geometry. Two-terminal
   components become circuitikz bipoles with verified polarity, transistors,
   gates and op-amps become circuitikz nodes, and everything else becomes a
   labelled rectangle whose pin stubs land on the true pin positions.

7. **`renderer.py`** — drives the local LaTeX toolchain found on `PATH`:
   `pdflatex` for PDF (with MiKTeX's `--enable-installer` when applicable),
   then `pdftocairo` (preferred) or `dvisvgm` for SVG and `pdftocairo` or
   `pdftoppm` for 300 dpi PNG. All tools are invoked with explicit argument
   lists (never `shell=True`) and a hard timeout; compiled PDFs are cached
   by `(tex path, mtime)` so rendering several formats compiles only once.

8. **`pipeline.py`** — `run_pipeline(PipelineOptions, progress=...)` is the
   single entry point used by both front ends: parse → build graph → write
   alt text → write `.tex` → render requested formats. It never raises for
   input problems; callers check `result.errors` / `result.warnings`.

9. **`cli.py` / `gui/`** — thin front ends over the pipeline. The CLI maps
   arguments to `PipelineOptions` and streams progress to stdout; the GUI
   runs the pipeline on a `QThread` worker and mirrors progress into an
   accessible log and the status bar.

## The two-layer data model

`model.py` defines both layers (quoting its module docstring):

> 1. **Document layer** — a faithful, geometry-preserving representation of
>    the `.kicad_sch` file (symbols, wires, junctions, labels...). Produced
>    by `schemaccess.kicad_parser`. Coordinates are KiCad schematic
>    coordinates: millimetres, **Y axis pointing down**.
>
> 2. **Circuit layer** — an electrical graph (components + nets) produced by
>    `schemaccess.netbuilder` from the document layer. Shared by the
>    alt-text generator and the CircuiTikZ generator.

Why two layers?

- The **document layer** (`SchematicDocument`, `SymbolInstance`, `Wire`,
  `Junction`, `Label`, ...) answers "what is drawn where". The CircuiTikZ
  generator needs it to reproduce the layout, and the net builder needs it
  to compute connectivity from geometry.
- The **circuit layer** (`CircuitGraph`, `Component`, `Net`,
  `PinConnection`) answers "what is connected to what". The analyzer and
  alt-text generator work exclusively on this layer — a description of a
  circuit should not depend on where things happen to be drawn.

A `CircuitGraph` keeps a reference to its source document
(`graph.document`), which is how `circuitikz.generate` gets both electrical
and geometric information from a single argument.

Coordinates are rounded to 4 decimal places (0.1 µm) by `model.snap()`
whenever they are used as connectivity keys, so floating-point noise can
never split a net.

## Connectivity rules

The net builder implements KiCad's connection semantics. Quoting the
`netbuilder.py` module docstring:

> Connectivity rules (matching KiCad semantics):
>
> * consecutive points of a wire polyline are connected;
> * wires sharing an endpoint are connected;
> * a junction connects everything passing through its position, including
>   wire interiors;
> * a pin, label or wire endpoint lying anywhere on a wire segment connects
>   to it;
> * two wires *crossing* mid-segment without a junction are NOT connected.
>
> Net names come from power symbols (GND, +5V, VCC...), then global labels,
> then hierarchical labels, then local labels; remaining nets get
> deterministic names N1, N2, ... ordered by their top-left-most point.

In addition to geometric connections, nets are joined **by name**: power
symbols and global labels join same-named nets anywhere in the schematic;
local labels join same-named nets on the same sheet; hierarchical labels
join same-named sheet pins (which is how flattened hierarchies connect).
Ground detection recognises the usual family of names (`GND`, `AGND`,
`DGND`, `VSS`, `EARTH`, `0`, ...), and a net carrying several power names
keeps the first sorted one and emits a warning.

## Determinism guarantees

Identical inputs always produce byte-identical outputs. Concretely:

- **No timestamps, no randomness** anywhere in the emitted text or LaTeX.
- **No reliance on unordered iteration**: every dict/set traversal that can
  affect output is sorted explicitly.
- **Net ordering** is defined: ground first, then power rails, then named,
  then anonymous nets; within a category by name, then by top-left-most
  point. Anonymous nets are numbered `N1, N2, ...` in that order.
- **Component reading order** (`CircuitGraph.sorted_components`) is defined:
  sources first, then natural reference order (`R1 < R2 < R10`).
- **Pin order** within a component: numeric pin numbers sort numerically,
  then non-numeric names lexicographically.
- The **analyzer** sorts every candidate list before pattern matching, so
  detected structures come out in a stable order.
- The **CircuiTikZ generator** emits wires and labels in document order and
  components sorted by reference, and formats coordinates with a fixed
  3-decimal rule (`_fmt`) that also normalises `-0.0`.
- The **CLI** reports output files in a fixed order
  (`alt_text, tex, pdf, svg, png`).

The only non-deterministic artefacts are the ones produced by external
tools (PDF/SVG/PNG binaries may embed tool-version metadata); everything
SchemAccess itself writes is reproducible.

## Extending: adding a new component mapping

Two tables control how a KiCad symbol becomes a drawn element:

1. **`model._LIB_NAME_MAP`** — a *list* of `(pattern, ComponentType)` pairs
   matched against the symbol's `lib_id` name (the part after the colon,
   lower-cased). Matching is by exact name, or by leading substring when the
   pattern ends in `_` (like `r_`, `c_`) or is longer than two characters
   (like `led`, `opamp`, `nand`). **Order matters** — the first match wins —
   so put specific patterns before generic ones (`r_potentiometer` sits
   above `r_`). If no pattern matches, `model._PREFIX_MAP` falls back to the
   reference-designator prefix (`R` → resistor, `Q` → transistor, ...).
2. **`circuitikz._BIPOLE_KEYS`** — maps a two-terminal `ComponentType` to a
   circuitikz bipole key (`R`, `C`, `leD`, ...). Multi-pin types instead use
   `_TRANSISTOR_STYLES`, `_GATE_STYLES`, the dedicated op-amp emitter, or
   the generic labelled-box fallback.

### Worked example: supporting `Device:Thermistor`

Suppose you want KiCad thermistor symbols to render as the circuitikz
thermistor bipole instead of a generic box.

**Step 1 — add a component type** (`src/schemaccess/model.py`):

```python
class ComponentType(enum.Enum):
    ...
    THERMISTOR = "thermistor"   # .value is the word used in alt text
```

**Step 2 — classify the symbol** (`src/schemaccess/model.py`). Add the
pattern to `_LIB_NAME_MAP` *above* the generic resistor entries, because
`thermistor` must win before any broader pattern could:

```python
_LIB_NAME_MAP = [
    ("thermistor", ComponentType.THERMISTOR),   # matches Thermistor, Thermistor_NTC, ...
    ("r_potentiometer", ComponentType.POTENTIOMETER),
    ...
]
```

(The pattern is longer than two characters, so it also matches as a leading
substring: `Device:Thermistor_NTC` → `thermistor_ntc` → matched.)

**Step 3 — mark it two-terminal** (`src/schemaccess/model.py`), so the
analyzer includes it in series/parallel detection and the CircuiTikZ
generator draws it as a bipole:

```python
_TWO_TERMINAL = {
    ...,
    ComponentType.THERMISTOR,
}
```

**Step 4 — map it to a circuitikz element**
(`src/schemaccess/circuitikz.py`):

```python
_BIPOLE_KEYS: Dict[ComponentType, str] = {
    ...,
    ComponentType.THERMISTOR: "thR",   # circuitikz thermistor bipole
}
```

Compile a small test document to verify the key renders the way you expect
(the existing keys were all verified against circuitikz 1.6+ this way).

**Step 5 — give it a unit for alt text** (`src/schemaccess/alttext.py`), so
`10k` reads as "10 kiloohm":

```python
_UNIT_FOR_TYPE: Dict[ComponentType, str] = {
    ...,
    ComponentType.THERMISTOR: "ohm",
}
```

**Step 6 — document and test.** Add a row to
`docs/component_mapping.md`, and add a fixture/test asserting both the
classification (`classify("Device:Thermistor") is ComponentType.THERMISTOR`)
and the emitted `to[thR, ...]` line.

That is the whole recipe: for a new *multi-pin* element you would instead
add a style entry (or a new emitter) in `circuitikz.py` step 4 — anything
left unmapped still renders safely as a labelled box with correct pin
positions.
