# Testing guide and requirement traceability

## How to run the tests

From the project root:

```
python -m pytest -q
```

To skip the slow tests that actually compile LaTeX (they invoke `pdflatex`
and the PDF converters, so they need the full toolchain and take seconds
per case):

```
python -m pytest -q -m "not slow"
```

Every test that compiles LaTeX or converts a PDF is marked
`@pytest.mark.slow`; everything else runs in well under a minute with no
external tools installed.

## Test data

The fixture corpus lives in `tests/fixtures/` — seventeen KiCad schematics
(fifteen valid ones listed in the manifest, plus `malformed.kicad_sch` and
`not_a_schematic.kicad_sch` for the error paths). Most are hand-built;
`big_200.kicad_sch` is produced by `gen_big.py`, and a few are real KiCad 10
files kept as regressions. `manifest.json` records the expected component count,
net count, ground presence and power-symbol count for every valid fixture
exactly as `netbuilder.build_graph` reports them. See
`tests/fixtures/README.md` for the full catalogue (reference RC divider,
voltage divider, RC filter, RLC series loop, LED + battery, inverting
op-amp, Wheatstone bridge, logic gates, a two-level hierarchy, two invalid
files, and a generated 200-component ladder for performance work).

## Requirement traceability matrix

> **Status: the pytest suite is still being written.** At the time of
> writing, `tests/` contains only the fixture corpus; no `test_*.py` files
> exist yet. The matrix below is therefore the **intended** mapping from
> the product test plan's requirement IDs to concrete test functions, and
> every row is marked *Planned*. Update this table with the real function
> names as the suite lands. The requirement wording below is paraphrased —
> the product test plan remains the authoritative source.

### Input / output requirements

| ID | Requirement (paraphrased) | Intended test function(s) | Status |
| --- | --- | --- | --- |
| I/O-1 | Valid KiCad `.kicad_sch` files (KiCad 6-9, including hierarchy) are read and parsed correctly. | `tests/test_kicad_parser.py::test_parse_all_valid_fixtures` (parses every valid fixture, asserts no exception); `test_parse_matches_manifest` (component/net/ground counts equal `manifest.json`); `test_hierarchy_flattening` (`hier_parent.kicad_sch` merges its child sheet) | Planned |
| I/O-2 | Invalid input is rejected with a clear error, without crashing. | `tests/test_kicad_parser.py::test_malformed_raises_parse_error` (`malformed.kicad_sch` → `KiCadParseError`); `test_wrong_document_type_raises` (`not_a_schematic.kicad_sch` → `KiCadParseError`); `test_missing_file_raises`; `tests/test_cli.py::test_bad_input_exit_code_1` (error message on stderr, exit code 1, no traceback) | Planned |
| I/O-3 | Outputs are written to the chosen folder (default `<input folder>/accessible`) with predictable names (`<stem>_alt_text.txt`, `<stem>.tex`, `<stem>.pdf/svg/png`). | `tests/test_pipeline.py::test_output_files_created_in_output_dir`; `tests/test_cli.py::test_default_output_dir_is_accessible_subfolder`; `test_output_file_naming` | Planned |
| I/O-4 | Image export supports PDF, SVG and PNG (individually and `all`). | `tests/test_renderer.py::test_render_pdf`, `test_render_svg`, `test_render_png` (all `@pytest.mark.slow`); `tests/test_pipeline.py::test_format_all_produces_three_files` (`slow`); `tests/test_cli.py::test_format_choices_rejects_unknown` (argparse exit 2) | Planned |

### Functional requirements

| ID | Requirement (paraphrased) | Intended test function(s) | Status |
| --- | --- | --- | --- |
| FUN-1 | Natural-language alt text describes the circuit's components and connectivity. | `tests/test_alttext.py::test_rc_divider_standard_text` (exact expected text for the reference fixture); `test_counts_sentence`; `test_source_polarity_sentence`; `test_value_formatting` (`4.7k` → "4.7 kiloohm", `22nF` → "22 nanofarad", `4k7`, `1MEG`) | Planned |
| FUN-2 | Three alt-text detail levels: `short`, `standard`, `detailed`. | `tests/test_alttext.py::test_short_level_counts_and_list_only`; `test_detailed_includes_structures_and_per_component_listing`; `test_unknown_level_falls_back_to_standard`; `tests/test_cli.py::test_detail_flag_passthrough` | Planned |
| FUN-3 | Generated CircuiTikZ/LaTeX compiles with `pdflatex` unmodified. | `tests/test_circuitikz.py::test_generate_is_complete_document` (documentclass/usepackage/begin-end present, LaTeX-escaped labels); `tests/test_renderer.py::test_every_valid_fixture_compiles` (`@pytest.mark.slow`) | Planned |
| FUN-4 | The rendering preserves the schematic's layout, values and labels. | `tests/test_circuitikz.py::test_layout_scaling_linear` (pin coordinates map linearly, Y flipped); `test_values_and_refs_annotated` (`l={R1}`, `a={20~$\Omega$}`); `test_diode_and_source_polarity`; `test_power_symbols_rendered` | Planned |
| FUN-5 | Circuit structures are detected: series/parallel, voltage dividers, RC/RL filters, Wheatstone bridges, op-amp configurations, logic gates, power rails. | `tests/test_analyzer.py::test_parallel_group_rc_divider`; `test_series_chain_rlc`; `test_voltage_divider_detected`; `test_rc_low_pass_detected`; `test_wheatstone_detected`; `test_opamp_inverting_detected`; `test_logic_gates_listed`; `test_power_rail_reported` | Planned |
| FUN-6 | The GUI lets the user pick a file, choose options, run the conversion and read results, and is screen-reader accessible. | `tests/test_gui.py` (requires PySide6; skipped when unavailable): `test_widgets_have_accessible_names`; `test_generate_disabled_until_input_and_output_selected`; `test_dependent_widgets_follow_checkboxes`; `test_make_pipeline_options_mapping`; `test_default_output_dir` | Planned |
| FUN-7 | The CLI exposes the documented flags and exit codes (0 success, 1 conversion error, 2 bad arguments). | `tests/test_cli.py::test_help_lists_all_flags`; `test_success_exit_code_0`; `test_print_alt_prints_text`; `test_quiet_suppresses_progress`; `test_no_alt_no_image_validation_pass`; `test_version_flag` | Planned |
| — | *(FUN IDs above are FUN-1..7 as listed in the product test plan.)* | | |

### Reliability requirements

| ID | Requirement (paraphrased) | Intended test function(s) | Status |
| --- | --- | --- | --- |
| REL-1 | Malformed or odd input never crashes: errors are reported, warnings collected, and processing continues where possible. | `tests/test_kicad_parser.py::test_malformed_constructs_warn_not_crash`; `tests/test_netbuilder.py::test_dangling_pin_warning`; `test_unknown_lib_symbol_warns`; `tests/test_pipeline.py::test_pipeline_never_raises_for_input_problems` (`result.errors` populated instead) | Planned |
| REL-2 | Unsupported/unknown symbols degrade gracefully to labelled boxes with correct pin positions, plus a warning. | `tests/test_circuitikz.py::test_unknown_component_becomes_labelled_box` (rectangle + pin stubs at true positions); `test_unrecognised_gate_pins_fall_back_to_box_with_warning` | Planned |
| REL-3 | A missing LaTeX toolchain is handled gracefully: alt text and `.tex` are still produced, with an install hint. | `tests/test_renderer.py::test_available_false_when_no_pdflatex` (monkeypatched `shutil.which`); `test_install_hint_platform_specific`; `tests/test_pipeline.py::test_missing_toolchain_writes_tex_and_warns` | Planned |
| PER-1 | Large schematics are processed in acceptable time. | `tests/test_performance.py::test_big_200_parses_and_builds_quickly` (`big_200.kicad_sch`: 200 components, 102 nets; parse + graph + alt text + tikz under a fixed time budget, no LaTeX) | Planned |
| COM-2 | The tool runs on both Windows and Linux/Unix (path handling, tool discovery, no platform-only APIs in the core). | `tests/test_pipeline.py::test_paths_use_os_join` (outputs land correctly regardless of separator); `tests/test_renderer.py::test_install_hint_platform_specific` (win/linux/darwin branches); the full suite running in CI on Windows and Linux is the primary evidence | Planned |

*(Only the IDs listed above are in scope for the automated suite; any other
requirement IDs in the product test plan are verified manually or by
inspection.)*

### Determinism (cross-cutting)

Determinism is a hard project rule rather than a numbered requirement, so
the intended suite checks it everywhere it matters:

- `tests/test_determinism.py::test_alt_text_identical_across_runs` and
  `test_tikz_identical_across_runs` — run every valid fixture through
  `alttext.generate` / `circuitikz.generate` twice (fresh parses) and
  assert byte-identical results;
- `test_fixture_generator_deterministic` — `gen_big.py` regenerates
  `big_200.kicad_sch` byte-identically.

## Notes for test authors

- Use `netbuilder.build_graph(kicad_parser.parse_file(path))` to get a
  graph; compare against `tests/fixtures/manifest.json` rather than
  hard-coding counts twice.
- The reference fixture `rc_divider.kicad_sch` has a fully documented
  expected graph (see the fixture README); prefer it for exact-text
  assertions.
- Register the `slow` marker in `pyproject.toml`
  (`[tool.pytest.ini_options] markers = ["slow: compiles LaTeX"]`) when the
  first slow test is added, so `-m "not slow"` stays warning-free.
- Renderer tests that need the toolchain should skip (not fail) when
  `Renderer().available()` is false, in addition to carrying the `slow`
  marker.
