from __future__ import annotations


def pretty_usd(value: float) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "$0.00"

    sign = "-" if v < 0 else ""
    v = abs(v)

    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:.2f}K"

    return f"{sign}${v:.2f}"


def pretty_bps(value: float) -> str:
    try:
        return f"{float(value):.2f} bps"
    except (TypeError, ValueError):
        return "0.00 bps"
