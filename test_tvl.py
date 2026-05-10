from apex_omega_core.core.polygon_arbitrage import PolygonDEXMonitor

bot = PolygonDEXMonitor()

usdc = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"

bot.token_metadata = {
    usdc.lower(): {
        "symbol": "USDC",
        "price_usd": 1.0,
        "decimals": 6,
    }
}

tvl, verified, breakdown = bot._compute_pool_tvl_usd_from_reserves(
    usdc,
    usdc,
    1_000_000_000,
    1_000_000_000,
)

print("TVL:", tvl)
print("Verified:", verified)
print("Breakdown:", breakdown)
