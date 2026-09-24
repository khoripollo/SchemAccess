"""Browser entry points: root_sheet(), convert() and build_zip()."""

from __future__ import annotations

import io
import json
import os
import time
import zipfile

from schemaccess import (alttext, circuitikz, kicad_parser, netbuilder,
                         netlist, pipeline)
from schemaccess import units as units_module

__all__ = ["root_sheet", "convert", "build_zip", "version"]

_LAST: dict = {}

_MIME = {
    "tex": "application/x-tex",
    "cir": "text/plain",
    "net": "text/plain",
    "txt": "text/plain",
    "csv": "text/csv",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}


def version() -> str:
    from schemaccess import __version__
    return __version__


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def root_sheet(paths) -> str:
    candidates = [str(p) for p in list(paths)
                  if str(p).lower().endswith(".kicad_sch")]
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    referenced = set()
    for path in candidates:
        try:
            doc = kicad_parser.parse_file(path, resolve_hierarchy=False)
        except kicad_parser.KiCadParseError:
            continue
        for sheet in doc.sheets:
            if sheet.filename:
                referenced.add(os.path.basename(sheet.filename).lower())

    roots = [p for p in candidates
             if os.path.basename(p).lower() not in referenced]
    return sorted(roots or candidates)[0]


def convert(path: str, junction_dots: bool = True,
            loops: bool = False, units: bool = False) -> str:
    global _LAST
    _LAST = {}

    try:
        started = time.perf_counter()
        doc = kicad_parser.parse_file(path)
    except kicad_parser.KiCadParseError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    except Exception as exc:
        return json.dumps({"ok": False,
                           "error": f"Could not read the file: {exc}"})

    graph = netbuilder.build_graph(doc)
    parse_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    fallbacks: set = set()
    drawn = units_module.apply(graph) if units else graph
    tex = circuitikz.generate(drawn, junction_dots=junction_dots,
                              fallbacks=fallbacks, loops=bool(loops))
    draw_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    descriptions = {level: alttext.generate(graph, level, loops=bool(loops))
                    for level in pipeline.DETAIL_LEVELS}
    text_ms = (time.perf_counter() - started) * 1000.0

    nets = {}
    notes = {}
    for fmt in netlist.FORMATS:
        result = netlist.generate(graph, fmt)
        nets[fmt] = result.text
        notes[fmt] = result.notes

    stats = pipeline.summarize(graph, tex, descriptions["detailed"],
                               fallbacks, loops=bool(loops))
    stats.parse_ms = parse_ms
    stats.draw_ms = draw_ms
    stats.text_ms = text_ms

    stem = _stem(path)
    files = [
        ("tex", f"{stem}.tex", "CircuiTikZ source", tex),
        ("spice", f"{stem}.cir", "SPICE deck", nets["spice"]),
        ("kicad", f"{stem}.net", "KiCad netlist", nets["kicad"]),
        ("text", f"{stem}_netlist.txt", "Readable net table", nets["text"]),
        ("csv", f"{stem}_netlist.csv", "One row per pin", nets["csv"]),
        ("alt", f"{stem}_alt_text.txt", "Description, detailed",
         descriptions["detailed"] + "\n"),
    ]

    payload = {
        "ok": True,
        "stem": stem,
        "source": os.path.basename(path),
        "tex": tex,
        "descriptions": descriptions,
        "netlists": nets,
        "netlist_notes": notes,
        "summary": stats.summary_lines(),
        "warnings": list(graph.warnings) + (
            [f"Loop currents are not drawn: {stats.loops_reason}."]
            if stats.loops_reason else []),
        "loops": {"requested": bool(loops), "drawn": stats.loops,
                  "reason": stats.loops_reason},
        "stats": {
            "symbols": stats.symbols,
            "components": stats.components,
            "nets": stats.nets,
            "nodes": stats.nodes,
            "drawn": stats.drawn,
            "described": stats.described,
            "fallbacks": stats.fallbacks,
            "undescribed": stats.undescribed,
            "convert_ms": round(stats.convert_ms, 1),
            "parse_ms": round(parse_ms, 1),
            "draw_ms": round(draw_ms, 1),
            "text_ms": round(text_ms, 1),
        },
        "files": [
            {"key": key, "name": name, "note": note,
             "bytes": len(body if isinstance(body, bytes)
                          else body.encode("utf-8")),
             "binary": isinstance(body, bytes),
             "mime": _MIME.get(name.rsplit(".", 1)[-1], "text/plain")}
            for key, name, note, body in files
        ],
    }

    _LAST = {"stem": stem, "files": files}
    return json.dumps(payload)


def build_zip(pdf: bytes = b"") -> bytes:
    if not _LAST:
        return b""
    buffer = io.BytesIO()
    stamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        entries = list(_LAST["files"])
        if pdf:
            entries.insert(0, ("pdf", f"{_LAST['stem']}.pdf",
                               "CircuiTikZ rendering", bytes(pdf)))
        for _key, name, _note, body in entries:
            info = zipfile.ZipInfo(f"{_LAST['stem']}/{name}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, body)
    return buffer.getvalue()
