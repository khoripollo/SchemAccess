"""Power symbols: the net each one names, and whether it is ground."""

from __future__ import annotations

import re
from typing import Optional

from .model import LibSymbol, SymbolInstance

_EMPTY_VALUES = ("", "~")  # KiCad writes an emptied field as ~

GROUND_NAMES = {"gnd", "gnda", "gndd", "gndref", "gndpwr", "agnd", "dgnd",
                "earth", "0", "gnds", "vss"}

_GROUND_HINT = re.compile(r"\b(?:ground|earth)\b", re.IGNORECASE)


def is_ground_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in GROUND_NAMES or lowered.startswith(("gnd", "earth"))


def is_blank(inst: SymbolInstance) -> bool:
    return inst.value.strip() in _EMPTY_VALUES


def net_name(inst: SymbolInstance) -> str:
    if is_blank(inst):
        return inst.lib_id.split(":", 1)[-1]
    return inst.value.strip()


def is_ground(inst: SymbolInstance, lib: Optional[LibSymbol]) -> bool:
    return (is_ground_name(net_name(inst))
            or is_ground_name(inst.lib_id.split(":", 1)[-1])
            or (lib is not None and bool(_GROUND_HINT.search(lib.hints))))
