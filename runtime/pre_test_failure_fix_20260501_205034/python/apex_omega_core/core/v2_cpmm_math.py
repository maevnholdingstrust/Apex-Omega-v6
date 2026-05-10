
from __future__ import annotations

def amount_out_cpmm(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int = 30) -> int:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    amount_in_with_fee = amount_in * (10_000 - fee_bps)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10_000 + amount_in_with_fee
    return numerator // denominator
