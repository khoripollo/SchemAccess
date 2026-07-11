"""Minimal, dependency-free S-expression reader for KiCad files.

KiCad 6+ files are UTF-8 S-expressions.  This module parses them into nested
Python lists where atoms are ``str``, ``int`` or ``float`` and quoted strings
are always ``str`` (even when they look numeric, e.g. a value ``"100"``).

To keep quoted strings distinguishable from bare tokens, quoted strings are
wrapped in :class:`QuotedString` (a ``str`` subclass).  Callers that don't
care can treat them as plain strings.
"""

from __future__ import annotations

from typing import List, Union

SExp = Union[str, int, float, list]


class QuotedString(str):
    """A string that appeared double-quoted in the source file."""

    __slots__ = ()


class SExprError(ValueError):
    """Raised when an S-expression file is malformed."""

    def __init__(self, message: str, line: int = 0, column: int = 0):
        self.line = line
        self.column = column
        if line:
            message = f"{message} (line {line}, column {column})"
        super().__init__(message)


def loads(text: str) -> list:
    """Parse *text* and return the single top-level S-expression list."""
    parser = _Parser(text)
    expr = parser.parse_expr()
    parser.skip_ws()
    if not parser.at_end():
        raise SExprError("Trailing content after top-level expression",
                         *parser.position())
    if not isinstance(expr, list):
        raise SExprError("Top-level expression must be a list")
    return expr


def load(path) -> list:
    """Parse the file at *path* (UTF-8, tolerating a BOM)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return loads(fh.read())


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def position(self):
        line = self.text.count("\n", 0, self.pos) + 1
        col = self.pos - (self.text.rfind("\n", 0, self.pos) + 1) + 1
        return line, col

    def at_end(self) -> bool:
        return self.pos >= self.length

    def skip_ws(self) -> None:
        while self.pos < self.length and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def parse_expr(self) -> SExp:
        self.skip_ws()
        if self.at_end():
            raise SExprError("Unexpected end of file", *self.position())
        ch = self.text[self.pos]
        if ch == "(":
            return self._parse_list()
        if ch == '"':
            return self._parse_string()
        if ch == ")":
            raise SExprError("Unexpected ')'", *self.position())
        return self._parse_atom()

    def _parse_list(self) -> list:
        self.pos += 1  # consume '('
        items: List[SExp] = []
        while True:
            self.skip_ws()
            if self.at_end():
                raise SExprError("Unterminated list", *self.position())
            if self.text[self.pos] == ")":
                self.pos += 1
                return items
            items.append(self.parse_expr())

    def _parse_string(self) -> QuotedString:
        self.pos += 1  # consume opening quote
        out: List[str] = []
        while True:
            if self.at_end():
                raise SExprError("Unterminated string", *self.position())
            ch = self.text[self.pos]
            if ch == '"':
                self.pos += 1
                return QuotedString("".join(out))
            if ch == "\\" and self.pos + 1 < self.length:
                nxt = self.text[self.pos + 1]
                mapped = {"n": "\n", "t": "\t", "r": "\r",
                          '"': '"', "\\": "\\"}.get(nxt)
                if mapped is not None:
                    out.append(mapped)
                    self.pos += 2
                    continue
            out.append(ch)
            self.pos += 1

    def _parse_atom(self) -> SExp:
        start = self.pos
        while (self.pos < self.length
               and self.text[self.pos] not in ' \t\r\n()"'):
            self.pos += 1
        token = self.text[start:self.pos]
        # Numeric conversion: KiCad writes coordinates and angles bare.
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            pass
        return token


# ---------------------------------------------------------------------------
# Convenience accessors used by the KiCad parser
# ---------------------------------------------------------------------------

def tag(expr: SExp) -> str:
    """Return the head token of a list expression, or '' otherwise."""
    if isinstance(expr, list) and expr and isinstance(expr[0], str):
        return str(expr[0])
    return ""


def children(expr: list, name: str) -> List[list]:
    """All child lists of *expr* whose head token equals *name*."""
    return [c for c in expr[1:]
            if isinstance(c, list) and c and c[0] == name]


def child(expr: list, name: str):
    """First child list with head *name*, or None."""
    for c in expr[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def atoms(expr: list) -> list:
    """Non-list items of *expr* after the head token."""
    return [c for c in expr[1:] if not isinstance(c, list)]
