"""Wire external data providers at import time."""

from apex_omega_core.scanner import dex_intake

try:
    from mygraph.client import fetch_pools, fetch_tokens
    from myprice.oracle import get_price as my_price_oracle
except ModuleNotFoundError:
    fetch_pools = None
    fetch_tokens = None
    my_price_oracle = None

if fetch_tokens is not None and fetch_pools is not None:
    dex_intake.TOKEN_UNIVERSE_PROVIDER = lambda: fetch_tokens(chain="polygon")
    dex_intake.POOL_STATE_PROVIDER = lambda tokens: fetch_pools(tokens, chain="polygon")

if my_price_oracle is not None:
    dex_intake.SPOT_PRICE_PROVIDER = lambda symbol: my_price_oracle(symbol)
