#!/usr/bin/env python3
"""
Test the flashloan provider filtering additional edge.
"""

from apex_omega_core.safety.execution_gates import (
    is_flashloan_provider_allowed,
    is_guaranteed_route,
    filter_routes_by_flashloan_provider,
    get_guaranteed_routes,
    gate_candidate
)

def test_flashloan_filtering():
    """Test flashloan provider filtering."""
    routes = [
        {'route_id': 10, 'flashloan_provider': 'curve', 'expected_profit': 140},
        {'route_id': 11, 'flashloan_provider': 'balancer', 'expected_profit': 95},
        {'route_id': 12, 'flashloan_provider': 'aave', 'expected_profit': 99},
        {'route_id': 13, 'flashloan_provider': 'balancer', 'expected_profit': 30}
    ]

    print('Testing flashloan provider filtering:')
    filtered = filter_routes_by_flashloan_provider(routes)
    print(f'Original routes: {len(routes)}')
    print(f'Filtered routes: {len(filtered)}')
    for route in filtered:
        print(f'  Route {route["route_id"]}: {route["flashloan_provider"]}')

    print('\nTesting guaranteed routes:')
    guaranteed = get_guaranteed_routes(filtered)
    print(f'Guaranteed routes (>= $25): {len(guaranteed)}')
    for route in guaranteed:
        print(f'  Route {route["route_id"]}: ${route["expected_profit"]}')

    print('\nTesting gate_candidate with blocked provider:')
    blocked_route = {
        'flashloan_provider': 'aave',
        'expected_profit': 50,
        'tvl_usd': 100000,
        'route_calldata': '0x',
        'reserve0': 1000,
        'reserve1': 1000,
        'reserves_verified': True,
        'reserve_staleness_seconds': 10,
        'rpc_healthy': True,
        'pool_type': 'V2',
        'type': 'V2'
    }
    gate_result = gate_candidate(blocked_route)
    print(f'Gate result: {gate_result.reason}')

    print('\nTesting gate_candidate with low profit:')
    low_profit_route = {
        'flashloan_provider': 'curve',
        'expected_profit': 10,
        'tvl_usd': 100000,
        'route_calldata': '0x',
        'reserve0': 1000,
        'reserve1': 1000,
        'reserves_verified': True,
        'reserve_staleness_seconds': 10,
        'rpc_healthy': True,
        'pool_type': 'V2',
        'type': 'V2'
    }
    gate_result = gate_candidate(low_profit_route)
    print(f'Gate result: {gate_result.reason}')

    print('\nTesting gate_candidate with good route:')
    good_route = {
        'flashloan_provider': 'curve',
        'expected_profit': 50,
        'tvl_usd': 100000,
        'route_calldata': '0x',
        'reserve0': 1000,
        'reserve1': 1000,
        'reserves_verified': True,
        'reserve_staleness_seconds': 10,
        'rpc_healthy': True,
        'pool_type': 'V2',
        'type': 'V2'
    }
    gate_result = gate_candidate(good_route)
    print(f'Gate result: accepted={gate_result.accepted}, reason={gate_result.reason}')

if __name__ == "__main__":
    test_flashloan_filtering()