from typing import List
from .types import InferenceResult, Feature


def profitability_gate(p_net: float, p_fill: float) -> bool:
    """Canonical profitability guardrail: execute only when P_net × P(fill) > 0.

    This is the single source of truth (SSOT) for the execution condition used
    throughout the pipeline.  Both factors must be strictly positive — a trade
    that is profitable but has zero fill probability, or one that will fill but
    loses money, must not be executed.

    Parameters
    ----------
    p_net:
        Net profit after all costs (fees, slippage, gas).  Must be > 0 for
        the gate to pass.
    p_fill:
        Probability of transaction inclusion in the next block, in [0.0, 1.0].
        Must be > 0 for the gate to pass.

    Returns
    -------
    bool
        ``True`` iff ``p_net > 0`` and ``p_fill > 0``.
    """
    return p_net > 0.0 and p_fill > 0.0


def derive_net_edge(data: dict, p_fill: float = 1.0) -> InferenceResult:
    """Derive net execution edge using the APEX-OMEGA v7 capital model formula.

    Capital identities (spec-locked):
      money_out         = buy_price  + buy_slippage
      money_in          = sell_price - sell_slippage
      edge              = money_in   - money_out
      adjusted_slippage = ml_slippage / 3
      EV_buffer         = raw_spread * buffer_rate * (trade_size / 100_000)
      net_edge          = edge - adjusted_slippage - EV_buffer - fees

    Execution condition (SSOT via :func:`profitability_gate`):
      should_execute = profitability_gate(net_edge, p_fill)

    Accepted keys in ``data`` (all optional, default to 0.0):
      buy_price, buy_slippage, sell_price, sell_slippage,
      ml_slippage, raw_spread, buffer_rate, trade_size, fees

    Parameters
    ----------
    data:
        Dict of capital model inputs (see above).
    p_fill:
        Probability of transaction inclusion in [0.0, 1.0].  Defaults to
        ``1.0`` for backward compatibility with callers that do not supply
        live gas / mempool data.

    Legacy single-key shortcut: if ``data`` only contains ``'edge'``, that
    value is used directly as ``net_edge`` so existing callers are unaffected.
    """
    # Legacy shortcut — preserves backward compatibility with callers that
    # supply a pre-computed 'edge' value without the full v7 breakdown.
    if set(data.keys()) <= {'edge'}:
        net_edge = float(data.get('edge', 0.0))
        features: List[Feature] = [
            Feature(name='edge', value=net_edge),
            Feature(name='confidence', value=0.95),
        ]
        return InferenceResult(
            net_edge=net_edge,
            features=features,
            should_execute=profitability_gate(net_edge, p_fill),
            p_fill=p_fill,
        )

    buy_price = float(data.get('buy_price', 0.0))
    buy_slippage = float(data.get('buy_slippage', 0.0))
    sell_price = float(data.get('sell_price', 0.0))
    sell_slippage = float(data.get('sell_slippage', 0.0))
    ml_slippage = float(data.get('ml_slippage', 0.0))
    raw_spread = float(data.get('raw_spread', 0.0))
    buffer_rate = float(data.get('buffer_rate', 0.0))
    trade_size = float(data.get('trade_size', 0.0))
    fees = float(data.get('fees', 0.0))

    money_out = buy_price + buy_slippage
    money_in = sell_price - sell_slippage
    edge = money_in - money_out
    adjusted_slippage = ml_slippage / 3.0
    ev_buffer = raw_spread * buffer_rate * (trade_size / 100_000.0)
    net_edge = edge - adjusted_slippage - ev_buffer - fees

    features = [
        Feature(name='buy_price', value=buy_price),
        Feature(name='buy_slippage', value=buy_slippage),
        Feature(name='sell_price', value=sell_price),
        Feature(name='sell_slippage', value=sell_slippage),
        Feature(name='money_out', value=money_out),
        Feature(name='money_in', value=money_in),
        Feature(name='edge', value=edge),
        Feature(name='adjusted_slippage', value=adjusted_slippage),
        Feature(name='ev_buffer', value=ev_buffer),
        Feature(name='fees', value=fees),
        Feature(name='net_edge', value=net_edge),
    ]
    return InferenceResult(
        net_edge=net_edge,
        features=features,
        should_execute=profitability_gate(net_edge, p_fill),
        p_fill=p_fill,
    )