"""Parser for KiCad 6/7/8/9 schematic files (``.kicad_sch``).

Extracts symbols, wires, junctions, labels, power symbols, no-connects and
hierarchical sheets into a :class:`schemaccess.model.SchematicDocument`.
Malformed or unsupported constructs produce warnings, never crashes.
"""

from __future__ import annotations

import os
from typing import List, Optional

from . import sexpr
from .model import (Junction, Label, LabelKind, LibSymbol, NoConnect, PinDef,
                    SchematicDocument, SheetRef, SymbolInstance, Wire, snap)


class KiCadParseError(ValueError):
    """Raised when a file cannot be read as a KiCad schematic at all."""


def parse_file(path: str, *, resolve_hierarchy: bool = True,
               _depth: int = 0) -> SchematicDocument:
    """Parse *path* into a :class:`SchematicDocument`.

    When *resolve_hierarchy* is true, referenced sub-sheets that exist on
    disk are parsed too and merged (flattened) into the returned document.
    """
    if not os.path.exists(path):
        raise KiCadParseError(f"File not found: {path}")
    if os.path.isdir(path):
        raise KiCadParseError(f"Not a file: {path}")
    try:
        root = sexpr.load(path)
    except sexpr.SExprError as exc:
        raise KiCadParseError(f"Malformed S-expression file: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise KiCadParseError(f"Cannot read file: {exc}") from exc

    if sexpr.tag(root) != "kicad_sch":
        raise KiCadParseError(
            f"Not a KiCad schematic (top-level tag is "
            f"'{sexpr.tag(root) or '?'}', expected 'kicad_sch')")

    doc = parse_document(root)
    doc.source_path = os.path.abspath(path)

    if resolve_hierarchy and doc.sheets and _depth < 8:
        _merge_subsheets(doc, path, _depth)
    return doc


def parse_document(root: list) -> SchematicDocument:
    """Parse an already-loaded ``kicad_sch`` S-expression."""
    doc = SchematicDocument()

    ver = sexpr.child(root, "version")
    if ver and len(ver) > 1 and isinstance(ver[1], (int, float)):
        doc.version = int(ver[1])
    gen = sexpr.child(root, "generator")
    if gen and len(gen) > 1:
        doc.generator = str(gen[1])

    libs = sexpr.child(root, "lib_symbols")
    if libs:
        for sym in sexpr.children(libs, "symbol"):
            lib = _parse_lib_symbol(sym, doc)
            if lib:
                doc.lib_symbols[lib.lib_id] = lib

    for node in root[1:]:
        t = sexpr.tag(node)
        try:
            if t == "symbol":
                inst = _parse_symbol_instance(node, doc)
                if inst:
                    doc.symbols.append(inst)
            elif t == "wire":
                w = _parse_wire(node)
                if w:
                    doc.wires.append(w)
            elif t == "bus":
                doc.warnings.append("Bus wires are treated as plain wires.")
                w = _parse_wire(node)
                if w:
                    doc.wires.append(w)
            elif t == "junction":
                at = _parse_at(node)
                if at:
                    doc.junctions.append(Junction(at[0], at[1]))
            elif t in ("label", "global_label", "hierarchical_label"):
                lbl = _parse_label(node, t)
                if lbl:
                    doc.labels.append(lbl)
            elif t == "no_connect":
                at = _parse_at(node)
                if at:
                    doc.no_connects.append(NoConnect(at[0], at[1]))
            elif t == "sheet":
                sheet = _parse_sheet(node)
                if sheet:
                    doc.sheets.append(sheet)
        except Exception as exc:  # noqa: BLE001 - robustness requirement
            doc.warnings.append(f"Skipped malformed '{t}' element: {exc}")
    return doc


# ---------------------------------------------------------------------------
# lib_symbols
# ---------------------------------------------------------------------------

def _parse_lib_symbol(node: list, doc: SchematicDocument) -> Optional[LibSymbol]:
    if len(node) < 2 or not isinstance(node[1], str):
        return None
    lib = LibSymbol(lib_id=str(node[1]))

    if sexpr.child(node, "power") is not None:
        lib.is_power = True

    for prop in sexpr.children(node, "property"):
        if len(prop) >= 3:
            key, val = str(prop[1]), str(prop[2])
            if key == "Reference":
                lib.reference_prefix = val
                if val == "#PWR" or val.startswith("#"):
                    lib.is_power = True
            elif key in ("Description", "ki_description"):
                lib.description = val

    # Pins live in nested unit sub-symbols named e.g.  "R_0_1", "R_1_1".
    for sub in sexpr.children(node, "symbol"):
        unit = _unit_of_subsymbol(str(sub[1]) if len(sub) > 1 else "")
        for pin in sexpr.children(sub, "pin"):
            pd = _parse_pin_def(pin, unit)
            if pd:
                lib.pins.append(pd)
    # Some symbols put pins directly at the top level.
    for pin in sexpr.children(node, "pin"):
        pd = _parse_pin_def(pin, 0)
        if pd:
            lib.pins.append(pd)
    return lib


def _unit_of_subsymbol(name: str) -> int:
    # "NAME_<unit>_<bodystyle>"; unit 0 means common to all units.
    parts = name.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return int(parts[1])
    return 0


def _parse_pin_def(node: list, unit: int) -> Optional[PinDef]:
    etype = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) \
        else "passive"
    at = sexpr.child(node, "at")
    if not at or len(at) < 3:
        return None
    x = float(at[1])
    y = float(at[2])
    orientation = float(at[3]) if len(at) > 3 else 0.0
    length = 0.0
    ln = sexpr.child(node, "length")
    if ln and len(ln) > 1:
        length = float(ln[1])
    name = "~"
    number = ""
    name_node = sexpr.child(node, "name")
    if name_node and len(name_node) > 1:
        name = str(name_node[1])
    num_node = sexpr.child(node, "number")
    if num_node and len(num_node) > 1:
        number = str(num_node[1])
    return PinDef(number=number, name=name, x=x, y=y,
                  orientation=orientation, length=length,
                  etype=etype, unit=unit)


# ---------------------------------------------------------------------------
# placed symbols
# ---------------------------------------------------------------------------

def _parse_symbol_instance(node: list,
                           doc: SchematicDocument) -> Optional[SymbolInstance]:
    lib_id_node = sexpr.child(node, "lib_id")
    if not lib_id_node or len(lib_id_node) < 2:
        doc.warnings.append("Symbol instance without lib_id skipped.")
        return None
    lib_id = str(lib_id_node[1])

    at = sexpr.child(node, "at")
    if not at or len(at) < 3:
        doc.warnings.append(f"Symbol '{lib_id}' without position skipped.")
        return None

    inst = SymbolInstance(
        uuid=_uuid_of(node),
        lib_id=lib_id,
        x=float(at[1]),
        y=float(at[2]),
        angle=float(at[3]) if len(at) > 3 else 0.0,
    )

    mirror = sexpr.child(node, "mirror")
    if mirror and len(mirror) > 1:
        inst.mirror = str(mirror[1])

    unit = sexpr.child(node, "unit")
    if unit and len(unit) > 1 and isinstance(unit[1], (int, float)):
        inst.unit = int(unit[1])

    if sexpr.child(node, "dnp") is not None:
        dnp = sexpr.child(node, "dnp")
        inst.dnp = not (len(dnp) > 1 and str(dnp[1]) == "no")

    for prop in sexpr.children(node, "property"):
        if len(prop) >= 3:
            key, val = str(prop[1]), str(prop[2])
            inst.properties[key] = val
            if key == "Reference":
                inst.reference = val
            elif key == "Value":
                inst.value = val
            elif key == "Footprint":
                inst.footprint = val
    return inst


def _uuid_of(node: list) -> str:
    u = sexpr.child(node, "uuid")
    if u and len(u) > 1:
        return str(u[1])
    return ""


# ---------------------------------------------------------------------------
# wires / labels / sheets
# ---------------------------------------------------------------------------

def _parse_wire(node: list) -> Optional[Wire]:
    pts = sexpr.child(node, "pts")
    if not pts:
        return None
    points = []
    for xy in sexpr.children(pts, "xy"):
        if len(xy) >= 3:
            points.append(snap(float(xy[1]), float(xy[2])))
    if len(points) < 2:
        return None
    return Wire(points=points, uuid=_uuid_of(node))


def _parse_at(node: list):
    at = sexpr.child(node, "at")
    if at and len(at) >= 3:
        return (float(at[1]), float(at[2]))
    return None


def _parse_label(node: list, kind: str) -> Optional[Label]:
    if len(node) < 2:
        return None
    text = str(node[1])
    at = _parse_at(node)
    if at is None:
        return None
    return Label(text=text, x=at[0], y=at[1], kind=LabelKind(kind))


def _parse_sheet(node: list) -> Optional[SheetRef]:
    name, filename = "", ""
    for prop in sexpr.children(node, "property"):
        if len(prop) >= 3:
            key, val = str(prop[1]), str(prop[2])
            if key in ("Sheetname", "Sheet name"):
                name = val
            elif key in ("Sheetfile", "Sheet file"):
                filename = val
    at = _parse_at(node) or (0.0, 0.0)
    sheet = SheetRef(name=name, filename=filename, x=at[0], y=at[1])
    for pin in sexpr.children(node, "pin"):
        if len(pin) >= 2:
            pin_at = _parse_at(pin)
            if pin_at:
                sheet.pins.append((str(pin[1]), snap(*pin_at)))
    return sheet


# ---------------------------------------------------------------------------
# hierarchy flattening
# ---------------------------------------------------------------------------

def _merge_subsheets(doc: SchematicDocument, path: str, depth: int) -> None:
    """Flatten sub-sheets into *doc*.

    Local labels inside a sub-sheet are namespaced with the sheet name so
    they do not accidentally join nets in other sheets.  Hierarchical labels
    keep their name: together with the parent's sheet pins (which KiCad
    places on the parent wires with the same name), same-named hierarchical
    ports connect parent and child - a practical, single-instance
    flattening.
    """
    base = os.path.dirname(os.path.abspath(path))
    top_sheets = list(doc.sheets)  # nested sheets appended below are
    for sheet in top_sheets:       # already flattened - don't re-process
        if not sheet.filename:
            continue
        sub_path = os.path.normpath(os.path.join(base, sheet.filename))
        if not os.path.exists(sub_path):
            doc.warnings.append(
                f"Sub-sheet file not found, skipped: {sheet.filename}")
            continue
        try:
            sub = parse_file(sub_path, resolve_hierarchy=True,
                             _depth=depth + 1)
        except KiCadParseError as exc:
            doc.warnings.append(
                f"Could not parse sub-sheet {sheet.filename}: {exc}")
            continue

        prefix = sheet.name or os.path.splitext(sheet.filename)[0]
        # Offset sub-sheet geometry far away from the parent so coordinates
        # can never collide; connectivity across the boundary is by label.
        span = 10000.0 * (top_sheets.index(sheet) + 1)
        for sym in sub.symbols:
            sym.on_sheet = prefix
            sym.x += span
            doc.symbols.append(sym)
        for wire in sub.wires:
            wire.points = [snap(px + span, py) for (px, py) in wire.points]
            doc.wires.append(wire)
        for junc in sub.junctions:
            junc.x += span
            doc.junctions.append(junc)
        for lbl in sub.labels:
            lbl.x += span
            lbl.on_sheet = prefix
            if lbl.kind == LabelKind.LOCAL:
                lbl.text = f"{prefix}/{lbl.text}"
            doc.labels.append(lbl)
        for nc in sub.no_connects:
            nc.x += span
            doc.no_connects.append(nc)
        for nested in sub.sheets:
            nested.pins = [(n, snap(px + span, py))
                           for (n, (px, py)) in nested.pins]
            doc.sheets.append(nested)
        for lid, lib in sub.lib_symbols.items():
            doc.lib_symbols.setdefault(lid, lib)
        doc.warnings.extend(sub.warnings)
