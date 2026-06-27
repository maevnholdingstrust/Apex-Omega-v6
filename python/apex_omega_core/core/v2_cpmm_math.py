
from __future__ import annotations

import math

BPS_SCALE = 10_000


def amount_out_cpmm(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int = 30) -> int:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    amount_in_with_fee = amount_in * (BPS_SCALE - fee_bps)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * BPS_SCALE + amount_in_with_fee
    return numerator // denominator


def amount_out_cpmm_float(
    amount_in: float,
    reserve_in: float,
    reserve_out: float,
    fee_bps: float = 30.0,
) -> float:
    """Decimal-normalized CPMM output for off-chain sizing/search.

    Integer calldata safety still belongs to protocol quotes and fork sim. This
    helper is for candidate sizing where reserves are already normalized.
    """
    if amount_in <= 0.0 or reserve_in <= 0.0 or reserve_out <= 0.0:
        return 0.0
    gamma = max(0.0, 1.0 - float(fee_bps) / BPS_SCALE)
    if gamma <= 0.0:
        return 0.0
    amount_in_with_fee = amount_in * gamma
    return (amount_in_with_fee * reserve_out) / (reserve_in + amount_in_with_fee)


def two_pool_cpmm_final_output(
    amount_in: float,
    reserve_in_a: float,
    reserve_out_a: float,
    reserve_in_b: float,
    reserve_out_b: float,
    fee_bps_a: float = 30.0,
    fee_bps_b: float = 30.0,
) -> float:
    """Final token output for an X->Y->X two-pool CPMM cycle."""
    mid_out = amount_out_cpmm_float(amount_in, reserve_in_a, reserve_out_a, fee_bps_a)
    return amount_out_cpmm_float(mid_out, reserve_in_b, reserve_out_b, fee_bps_b)


def two_pool_cpmm_profit(
    amount_in: float,
    reserve_in_a: float,
    reserve_out_a: float,
    reserve_in_b: float,
    reserve_out_b: float,
    fee_bps_a: float = 30.0,
    fee_bps_b: float = 30.0,
) -> float:
    """Gross cycle profit before gas/flashloan/protocol friction."""
    return two_pool_cpmm_final_output(
        amount_in,
        reserve_in_a,
        reserve_out_a,
        reserve_in_b,
        reserve_out_b,
        fee_bps_a,
        fee_bps_b,
    ) - amount_in


def two_pool_cpmm_optimal_input(
    reserve_in_a: float,
    reserve_out_a: float,
    reserve_in_b: float,
    reserve_out_b: float,
    fee_bps_a: float = 30.0,
    fee_bps_b: float = 30.0,
) -> float:
    """Closed-form gross-profit optimum for an X->Y->X CPMM arbitrage.

    Pool A swaps X->Y. Pool B swaps Y->X. The coupled output simplifies to:

        out = K*x / (M + N*x)

    where K = D*g1*g2*B, M = C*A, N = g1*(C + g2*B). Maximizing
    out - x gives x = (sqrt(K*M) - M) / N when K > M.
    """
    a = float(reserve_in_a)
    b = float(reserve_out_a)
    c = float(reserve_in_b)
    d = float(reserve_out_b)
    if min(a, b, c, d) <= 0.0:
        return 0.0

    g1 = max(0.0, 1.0 - float(fee_bps_a) / BPS_SCALE)
    g2 = max(0.0, 1.0 - float(fee_bps_b) / BPS_SCALE)
    if g1 <= 0.0 or g2 <= 0.0:
        return 0.0

    k = d * g1 * g2 * b
    m = c * a
    n = g1 * (c + g2 * b)
    if k <= m or n <= 0.0:
        return 0.0

    optimum = (math.sqrt(k * m) - m) / n
    if not math.isfinite(optimum) or optimum <= 0.0:
        return 0.0
    return optimum
