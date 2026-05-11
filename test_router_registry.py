from apex_omega_core.core.dex_router_registry import get_router_address, is_known_router

print(get_router_address("quickswap-v2"))
print(get_router_address("uniswap-v3"))
print(is_known_router("0xa5E0829CaCED8fFDD4De3c43696c57F7D7A678ff"))
