"""Average runs per day for a cron expression (5 fields or @macros)."""

from __future__ import annotations

MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
          "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
          "@hourly": "0 * * * *"}


def _count(field: str, lo: int, hi: int) -> int:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = int(part)
            b = hi if step > 1 else a
        if not (lo <= a <= hi and lo <= b <= hi):
            raise ValueError(f"cron field out of range: {field}")
        values.update(range(a, b + 1, step))
    return len(values)


def runs_per_day(expr: str) -> float:
    expr = MACROS.get(expr.strip(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 cron fields, got {expr!r}")
    minute, hour, dom, month, dow = parts
    per_day = _count(minute, 0, 59) * _count(hour, 0, 23)
    frac = 1.0
    if dom != "*":
        frac *= _count(dom, 1, 31) / 30.4
    if dow != "*":
        frac *= _count(dow.replace("7", "0"), 0, 6) / 7
    if month != "*":
        frac *= _count(month, 1, 12) / 12
    return per_day * min(frac, 1.0)
