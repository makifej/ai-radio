"""Minimális TOML-író a radio.toml szerkezetéhez (a tomllib csak olvasni tud).

Felső szinten: egyszerű értékek, táblák ([news]) és táblatömbök ([[dayparts]]).
Táblán belül a szótár inline táblaként, a szótárlista többsoros tömbként kerül
ki. A megjegyzések mentéskor elvesznek.
"""

from __future__ import annotations

import json
import re

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k, ensure_ascii=False)


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        text = repr(v)
        return text if any(c in text for c in ".eE") else text + ".0"
    if isinstance(v, str):
        # A JSON-karakterlánc a TOML alap karakterláncával kompatibilis.
        return json.dumps(v, ensure_ascii=False)
    raise TypeError(f"Nem írható TOML-ba: {type(v).__name__}")


def _value(v) -> str:
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            return "[\n" + "".join(f"    {_value(x)},\n" for x in v) + "]"
        return "[" + ", ".join(_value(x) for x in v) + "]"
    return _scalar(v)


def _is_table_array(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def dumps(data: dict) -> str:
    plain = {k: v for k, v in data.items() if not isinstance(v, dict) and not _is_table_array(v)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    arrays = {k: v for k, v in data.items() if _is_table_array(v)}

    lines = [f"{_key(k)} = {_value(v)}" for k, v in plain.items()]
    for name, table in tables.items():
        lines += ["", f"[{_key(name)}]"] + [f"{_key(k)} = {_value(v)}" for k, v in table.items()]
    for name, items in arrays.items():
        for item in items:
            lines += ["", f"[[{_key(name)}]]"] + [f"{_key(k)} = {_value(v)}" for k, v in item.items()]
    return "\n".join(lines).lstrip("\n") + "\n"
