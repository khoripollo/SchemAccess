"""PDF output: the circuit drawing as a real vector PDF, without LaTeX.

:mod:`schemaccess.renderer` makes a PDF by running ``pdflatex`` over the
CircuiTikZ document, which is the authoritative rendering but needs a TeX
toolchain.  There is no TeX in a browser, so this module writes the PDF
itself: it is a few hundred lines of the PDF imaging model (paths,
transforms and the base-14 fonts), all standard library, and it produces
a genuine vector file that scales, prints and drops into a document.

It shares its drawing code with :mod:`schemaccess.svgpreview` rather than
re-implementing it.  :func:`schemaccess.svgpreview.draw` takes any object
with the canvas method set, so :class:`PdfCanvas` below is handed to the
same routines that draw the SVG.  The two outputs therefore cannot drift
apart: a symbol fixed in one is fixed in both.

Coordinates
-----------
PDF's Y axis points up and its unit is the point; KiCad's Y axis points
down and its unit is the millimetre.  Rather than convert every
coordinate, the content stream opens with one transform that makes user
space *be* schematic millimetres, so the drawing code passes its numbers
through untouched.  Text is the one exception: glyphs would be upside
down under a flipped axis, so each text object carries a matrix that
flips them back.

Output is deterministic: no creation date, no producer string, no
object shuffling.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import svgpreview
from .model import CircuitGraph, Point

__all__ = ["generate", "PdfCanvas"]

#: PostScript points per millimetre.
PT_PER_MM = 72.0 / 25.4

# Stroke widths in millimetres, keyed by the class the drawing code uses.
_WIDTHS = {
    "sa-wire": 0.18,
    "sa-lead": 0.18,
    "sa-body": 0.22,
    "sa-thick": 0.22 * 1.8,
}

#: Grey used for the things the SVG renders at reduced opacity.
_MUTED_GREY = 0.42

# Character widths for Helvetica, in 1/1000 em.  Only the metrics that
# affect centring are needed, so this is a compact approximation by
# character class rather than the full AFM table: label text is a few
# characters long, and a percent or two of error is invisible.
_W_DEFAULT = 556
_WIDTH_CLASSES: List[Tuple[str, int]] = [
    (" ", 278),
    ("iljI.,:;'|!", 250),
    ("ftr()[]{}/\\-", 333),
    ("\"", 355),
    ("abcdeghknopqsuvxyz0123456789+<>=~^", 556),
    ("ABCDEFGHKLNPRSTUVXYZ&$", 667),
    ("mMWw%@", 889),
    ("OQ#", 778),
]
_WIDTHS_BY_CHAR: Dict[str, int] = {}
for _chars, _w in _WIDTH_CLASSES:
    for _ch in _chars:
        _WIDTHS_BY_CHAR[_ch] = _w

# Characters outside ASCII that WinAnsiEncoding does carry, as octal
# escapes.  Anything not listed here and not in the Symbol map below is
# dropped rather than written as a wrong glyph.
_WINANSI = {
    "µ": r"\265", "μ": r"\265", "°": r"\260", "±": r"\261",
    "×": r"\327", "÷": r"\367", "²": r"\262", "³": r"\263",
    "·": r"\267", "–": "-", "—": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "−": "-",
}

# Glyphs only the Symbol font has.  Omega turns up on every resistor, and
# it arrives as either codepoint: U+2126 is the OHM SIGN the drawing code
# writes, U+03A9 the Greek capital letter.  The two are indistinguishable
# on screen, so they are spelled by escape rather than pasted -- writing
# one and matching the other is how the ohm sign went missing once.
_SYMBOL = {
    "Ω": "W",      # OHM SIGN
    "Ω": "W",      # GREEK CAPITAL LETTER OMEGA
    "π": "p",      # pi
    "ω": "w",      # omega
    "Δ": "D",      # Delta
    "α": "a",      # alpha
    "β": "b",      # beta
    "θ": "q",      # theta
    "λ": "l",      # lambda
    "φ": "j",      # phi
    "∠": "\320",   # angle
}


def _fmt(value: float) -> str:
    """Deterministic short number formatting for a content stream."""
    value = round(value, 3)
    if value == 0:
        value = 0.0                       # normalise -0.0
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------------------
# Path data
# ---------------------------------------------------------------------------

def _tokens(data: str) -> List[str]:
    out: List[str] = []
    number = ""
    for char in data:
        if char in "MLQAZmlqaz":
            if number:
                out.append(number)
                number = ""
            out.append(char.upper())
        elif char in "0123456789.eE" or (
                char == "-" and (not number or number[-1] in "eE")):
            number += char
        else:
            if number:
                out.append(number)
                number = ""
    if number:
        out.append(number)
    return out


def _quad_to_cubic(p0: Point, control: Point, p1: Point):
    """A quadratic Bezier as the cubic PDF understands."""
    c1 = (p0[0] + 2.0 / 3.0 * (control[0] - p0[0]),
          p0[1] + 2.0 / 3.0 * (control[1] - p0[1]))
    c2 = (p1[0] + 2.0 / 3.0 * (control[0] - p1[0]),
          p1[1] + 2.0 / 3.0 * (control[1] - p1[1]))
    return c1, c2


def _arc_to_cubics(p0: Point, rx: float, ry: float, rotation: float,
                   large_arc: bool, sweep: bool, p1: Point):
    """Convert an SVG elliptical arc to a list of cubic segments.

    The endpoint parameterisation SVG uses has to be turned into a centre
    and a sweep before it can be approximated by Beziers; this is the
    conversion from the SVG specification's implementation notes.
    """
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [("line", p1)]

    phi = math.radians(rotation)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx = (p0[0] - p1[0]) / 2.0
    dy = (p0[1] - p1[1]) / 2.0
    x1 = cos_phi * dx + sin_phi * dy
    y1 = -sin_phi * dx + cos_phi * dy

    # Scale the radii up if they are too small to span the two points.
    over = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if over > 1:
        scale = math.sqrt(over)
        rx *= scale
        ry *= scale

    numerator = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    denominator = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = math.sqrt(max(numerator / denominator, 0.0)) if denominator else 0.0
    if large_arc == sweep:
        factor = -factor
    cx1 = factor * rx * y1 / ry
    cy1 = -factor * ry * x1 / rx

    cx = cos_phi * cx1 - sin_phi * cy1 + (p0[0] + p1[0]) / 2.0
    cy = sin_phi * cx1 + cos_phi * cy1 + (p0[1] + p1[1]) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        det = ux * vy - uy * vx
        return math.atan2(det, dot)

    start_x, start_y = (x1 - cx1) / rx, (y1 - cy1) / ry
    end_x, end_y = (-x1 - cx1) / rx, (-y1 - cy1) / ry
    theta = angle(1.0, 0.0, start_x, start_y)
    delta = angle(start_x, start_y, end_x, end_y)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    segments = max(1, int(math.ceil(abs(delta) / (math.pi / 2))))
    step = delta / segments
    # The magic constant that makes a cubic hug a circular arc.
    alpha = 4.0 / 3.0 * math.tan(step / 4.0)

    out = []
    angle_at = theta
    for _ in range(segments):
        next_angle = angle_at + step
        cos_a, sin_a = math.cos(angle_at), math.sin(angle_at)
        cos_b, sin_b = math.cos(next_angle), math.sin(next_angle)

        def place(ux: float, uy: float) -> Point:
            return (cos_phi * rx * ux - sin_phi * ry * uy + cx,
                    sin_phi * rx * ux + cos_phi * ry * uy + cy)

        point_a = place(cos_a, sin_a)
        point_b = place(cos_b, sin_b)
        control1 = place(cos_a - alpha * sin_a, sin_a + alpha * cos_a)
        control2 = place(cos_b + alpha * sin_b, sin_b - alpha * cos_b)
        # Only the control points and the endpoint are emitted; the
        # current point is already where point_a is.
        del point_a
        out.append(("curve", control1, control2, point_b))
        angle_at = next_angle
    return out


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _runs(text: str) -> List[Tuple[str, str]]:
    """Split *text* into (font key, encoded string) runs.

    ``"20Ω"`` becomes a Helvetica run and a Symbol run, because the
    base-14 Helvetica has no Omega.  Characters in neither encoding are
    dropped: a missing glyph beats a wrong one.
    """
    out: List[Tuple[str, List[str]]] = []

    def push(font: str, piece: str) -> None:
        if out and out[-1][0] == font:
            out[-1][1].append(piece)
        else:
            out.append((font, [piece]))

    for char in str(text):
        if char in "()\\":
            push("text", "\\" + char)
        elif 32 <= ord(char) < 127:
            push("text", char)
        elif char in _WINANSI:
            push("text", _WINANSI[char])
        elif char in _SYMBOL:
            push("symbol", _SYMBOL[char])
        # anything else is dropped
    return [(font, "".join(pieces)) for font, pieces in out]


def _run_width(font: str, encoded: str, size: float) -> float:
    """Width of an encoded run in millimetres."""
    total = 0
    index = 0
    while index < len(encoded):
        char = encoded[index]
        if char == "\\":
            # An escape: either \( \) \\ or a three-digit octal code.
            if index + 3 < len(encoded) and encoded[index + 1].isdigit():
                index += 4
            else:
                index += 2
            total += _W_DEFAULT
            continue
        total += (600 if font == "symbol"
                  else _WIDTHS_BY_CHAR.get(char, _W_DEFAULT))
        index += 1
    return total / 1000.0 * size


# ---------------------------------------------------------------------------
# The canvas
# ---------------------------------------------------------------------------

class PdfCanvas:
    """A drawing surface with the method set :func:`svgpreview.draw` uses.

    Everything is recorded in schematic millimetres; the page transform
    applied in :func:`generate` turns them into points.
    """

    def __init__(self) -> None:
        self.wires: List[str] = []
        self.bodies: List[str] = []
        self.text: List[str] = []
        self._width: Optional[float] = None
        self._grey: Optional[float] = None

    # -- state -----------------------------------------------------------
    def _target(self, cls: str) -> List[str]:
        return self.wires if cls in ("sa-wire", "sa-dot") else self.bodies

    def _style(self, target: List[str], cls: str) -> bool:
        """Emit any needed state change; return True when this is a fill."""
        filled = "sa-filled" in cls or "sa-dot" in cls
        grey = _MUTED_GREY if "sa-ghost" in cls else 0.0
        if grey != self._grey:
            target.append(f"{_fmt(grey)} g {_fmt(grey)} G")
            self._grey = grey
        if not filled:
            width = _WIDTHS["sa-thick"] if "sa-thick" in cls else next(
                (w for key, w in _WIDTHS.items() if key in cls), 0.22)
            if width != self._width:
                target.append(f"{_fmt(width)} w")
                self._width = width
        return filled

    # -- primitives ------------------------------------------------------
    def line(self, a: Point, b: Point, cls: str = "sa-wire") -> None:
        target = self._target(cls)
        self._style(target, cls)
        target.append(f"{_fmt(a[0])} {_fmt(a[1])} m "
                      f"{_fmt(b[0])} {_fmt(b[1])} l S")

    def polyline(self, points: Sequence[Point],
                 cls: str = "sa-wire") -> None:
        if len(points) < 2:
            return
        target = self._target(cls)
        self._style(target, cls)
        parts = [f"{_fmt(points[0][0])} {_fmt(points[0][1])} m"]
        for point in points[1:]:
            parts.append(f"{_fmt(point[0])} {_fmt(point[1])} l")
        parts.append("S")
        target.append(" ".join(parts))

    def circle(self, centre: Point, radius: float,
               cls: str = "sa-body") -> None:
        target = self._target(cls)
        filled = self._style(target, cls)
        kappa = 0.5523 * radius
        cx, cy = centre
        target.append(
            f"{_fmt(cx + radius)} {_fmt(cy)} m "
            f"{_fmt(cx + radius)} {_fmt(cy + kappa)} "
            f"{_fmt(cx + kappa)} {_fmt(cy + radius)} "
            f"{_fmt(cx)} {_fmt(cy + radius)} c "
            f"{_fmt(cx - kappa)} {_fmt(cy + radius)} "
            f"{_fmt(cx - radius)} {_fmt(cy + kappa)} "
            f"{_fmt(cx - radius)} {_fmt(cy)} c "
            f"{_fmt(cx - radius)} {_fmt(cy - kappa)} "
            f"{_fmt(cx - kappa)} {_fmt(cy - radius)} "
            f"{_fmt(cx)} {_fmt(cy - radius)} c "
            f"{_fmt(cx + kappa)} {_fmt(cy - radius)} "
            f"{_fmt(cx + radius)} {_fmt(cy - kappa)} "
            f"{_fmt(cx + radius)} {_fmt(cy)} c "
            + ("f" if filled else "S"))

    def rect(self, x: float, y: float, w: float, h: float,
             cls: str = "sa-body", radius: float = 0.0) -> None:
        target = self._target(cls)
        filled = self._style(target, cls)
        target.append(f"{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} re "
                      + ("f" if filled else "S"))

    def path(self, data: str, cls: str = "sa-body") -> None:
        target = self._target(cls)
        filled = self._style(target, cls)
        parts: List[str] = []
        tokens = _tokens(data)
        index = 0
        current: Point = (0.0, 0.0)
        closed = False

        def number() -> float:
            nonlocal index
            value = float(tokens[index])
            index += 1
            return value

        while index < len(tokens):
            command = tokens[index]
            index += 1
            if command == "M":
                current = (number(), number())
                parts.append(f"{_fmt(current[0])} {_fmt(current[1])} m")
            elif command == "L":
                current = (number(), number())
                parts.append(f"{_fmt(current[0])} {_fmt(current[1])} l")
            elif command == "Q":
                control = (number(), number())
                end = (number(), number())
                c1, c2 = _quad_to_cubic(current, control, end)
                parts.append(
                    f"{_fmt(c1[0])} {_fmt(c1[1])} {_fmt(c2[0])} {_fmt(c2[1])} "
                    f"{_fmt(end[0])} {_fmt(end[1])} c")
                current = end
            elif command == "A":
                rx, ry, rotation = number(), number(), number()
                large_arc, sweep = bool(number()), bool(number())
                end = (number(), number())
                for segment in _arc_to_cubics(current, rx, ry, rotation,
                                              large_arc, sweep, end):
                    if segment[0] == "line":
                        parts.append(f"{_fmt(segment[1][0])} "
                                     f"{_fmt(segment[1][1])} l")
                    else:
                        _kind, c1, c2, point = segment
                        parts.append(
                            f"{_fmt(c1[0])} {_fmt(c1[1])} "
                            f"{_fmt(c2[0])} {_fmt(c2[1])} "
                            f"{_fmt(point[0])} {_fmt(point[1])} c")
                current = end
            elif command == "Z":
                parts.append("h")
                closed = True

        if not parts:
            return
        parts.append("f" if filled else ("s" if closed else "S"))
        target.append(" ".join(parts))

    def label(self, position: Point, content: str,
              *, size: float = 1.5, anchor: str = "middle",
              cls: str = "sa-text", baseline: str = "middle") -> None:
        if not content:
            return
        runs = _runs(content)
        if not runs:
            return
        total = sum(_run_width(font, encoded, size) for font, encoded in runs)
        x = position[0]
        if anchor == "middle":
            x -= total / 2.0
        elif anchor == "end":
            x -= total
        # The page transform flips Y, so the text matrix flips the glyphs
        # back upright; the baseline sits a little below the centre line.
        y = position[1] + (0.36 * size if baseline == "middle" else 0.0)

        italic = "sa-netname" in cls
        grey = _MUTED_GREY if italic else 0.0
        out = ["BT", f"{_fmt(grey)} g",
               f"{_fmt(size)} 0 0 {_fmt(-size)} {_fmt(x)} {_fmt(y)} Tm"]
        for font, encoded in runs:
            name = "/F3" if font == "symbol" else ("/F2" if italic else "/F1")
            out.append(f"{name} 1 Tf ({encoded}) Tj")
        out.append("ET")
        self.text.append(" ".join(out))
        self._grey = None                 # BT/ET left the colour changed

    # -- local frames ----------------------------------------------------
    def open_frame(self, origin: Point, direction: Point) -> None:
        angle = math.atan2(direction[1], direction[0])
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        self.bodies.append(
            f"q {_fmt(cos_a)} {_fmt(sin_a)} {_fmt(-sin_a)} {_fmt(cos_a)} "
            f"{_fmt(origin[0])} {_fmt(origin[1])} cm")
        self._width = None
        self._grey = None

    def close_frame(self) -> None:
        self.bodies.append("Q")
        self._width = None
        self._grey = None


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def _pdf_string(text: str) -> str:
    return "(" + str(text).replace("\\", r"\\").replace("(", r"\(") \
        .replace(")", r"\)") + ")"


def generate(graph: CircuitGraph, *, junction_dots: bool = True,
             loops: bool = False) -> bytes:
    """Return the circuit drawing as a single-page PDF.

    The page is sized to the schematic, so the drawing fills it with the
    same margin the SVG uses and nothing has to be scaled by hand
    afterwards.
    """
    canvas = PdfCanvas()
    bounds = svgpreview.draw(graph, canvas, junction_dots=junction_dots,
                             loops=loops)

    page_w = bounds.width * PT_PER_MM
    page_h = bounds.height * PT_PER_MM

    # One transform makes user space schematic millimetres, Y downward.
    setup = (f"q {_fmt(PT_PER_MM)} 0 0 {_fmt(-PT_PER_MM)} "
             f"{_fmt(-bounds.min_x * PT_PER_MM)} "
             f"{_fmt((bounds.min_y + bounds.height) * PT_PER_MM)} cm\n"
             "1 J 1 j\n")
    body = "\n".join(canvas.wires + canvas.bodies + canvas.text)
    # The end-of-line before ``endstream`` is a delimiter, not data, so
    # the stream must not end with one or /Length is out by a byte.
    stream = (setup + body + "\nQ").encode("latin-1", "replace")

    objects: List[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R "
         f"/MediaBox [0 0 {_fmt(page_w)} {_fmt(page_h)}] "
         "/Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R >> >> "
         "/Contents 4 0 R >>").encode("latin-1"),
        (f"<< /Length {len(stream)} >>\nstream\n").encode("latin-1")
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique "
        b"/Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Symbol >>",
        ("<< /Title " + _pdf_string(bounds.title)
         + " /Creator (SchemAccess) >>").encode("latin-1", "replace"),
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1")
        out += payload
        out += b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R "
            f"/Info {len(objects)} 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode("latin-1")
    return bytes(out)
