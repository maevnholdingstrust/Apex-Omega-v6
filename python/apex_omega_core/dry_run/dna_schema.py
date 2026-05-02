from typing import Any, Dict


def build_c1_card(key, simulated_net_usd: float) -> Dict[str, Any]:
    return {
        'identity': {
            'card_id': f"{key.opportunity_id}_c1",
            'cycle_id': key.cycle_id,
            'global_cycle_number': key.global_cycle_number,
            'block_cycle_number': key.block_cycle_number,
            'block_number': key.block_number,
            'block_id': key.block_id,
            'opportunity_id': key.opportunity_id,
            'strike_role': 'C1',
            'strike_name': 'Aggressor',
            'decision': 'BUILD_PAYLOAD',
        },
        'decision': {'payload_built': True, 'realized_status': 'DRY_RUN_NO_BROADCAST'},
        'math': {'net_profit_usd': simulated_net_usd},
        'payload': {'would_sign': False, 'would_broadcast': False},
    }


def build_c2_card(key, c1_card_id: str, decision: str, simulated_net_usd: float, no_op_reason: str = '') -> Dict[str, Any]:
    card = {
        'identity': {
            'card_id': f"{key.opportunity_id}_c2",
            'cycle_id': key.cycle_id,
            'global_cycle_number': key.global_cycle_number,
            'block_cycle_number': key.block_cycle_number,
            'block_number': key.block_number,
            'block_id': key.block_id,
            'opportunity_id': key.opportunity_id,
            'trigger': c1_card_id,
            'strike_role': 'C2',
            'strike_name': 'Surgeon',
            'decision': decision,
        },
        'decision': {'payload_built': decision == 'EXECUTE', 'realized_status': 'DRY_RUN_NO_BROADCAST'},
        'math': {'net_profit_usd': simulated_net_usd},
        'payload': {'would_sign': False, 'would_broadcast': False},
    }
    if decision == 'NO_OP':
        card['decision']['no_op_reason'] = no_op_reason or 'EV<=0'
    return card
