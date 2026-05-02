import json
from pathlib import Path

from .block_cycle_index import BlockCycleIndex
from .dna_logger import DnaLogger
from .dna_schema import build_c1_card, build_c2_card
from .realtime_bus import RealtimeBus
from apex_omega_core.safety.dry_run_guard import enforce_no_broadcast_env, assert_dry_run_env


class DryRunOrchestrator:
    def __init__(self, log_dir: str = 'logs'):
        self.log = DnaLogger(log_dir)
        self.bus = RealtimeBus()
        self.index = BlockCycleIndex()

    def run(self, limit: int = 20) -> dict:
        enforce_no_broadcast_env()
        assert_dry_run_env()
        cycles = []
        self._emit('DRY_RUN_STARTED', {'limit': limit})
        for i in range(limit):
            block = 73491288 + (i // 3)
            key = self.index.next(block)
            c1_net = round(10 + (i % 5) * 1.1, 2)
            c2_decision = 'EXECUTE' if i % 4 == 0 else 'NO_OP'
            c2_net = round(1.5, 2) if c2_decision == 'EXECUTE' else 0.0

            c1 = build_c1_card(key, c1_net)
            c2 = build_c2_card(key, c1['identity']['card_id'], c2_decision, c2_net)
            self.log.dna_card(c1)
            self.log.dna_card(c2)
            self.log.payload({'opportunity_id': key.opportunity_id, 'strike': 'C1'})
            self._emit('DNA_CARD_CREATED', {'card_id': c1['identity']['card_id']})
            self._emit('DNA_CARD_CREATED', {'card_id': c2['identity']['card_id']})
            if c2_decision == 'NO_OP':
                self._emit('C2_NO_OP', {'opportunity_id': key.opportunity_id})

            pair = {
                'block_number': block,
                'block_id': key.block_id,
                'block_cycle_number': key.block_cycle_number,
                'global_cycle_number': key.global_cycle_number,
                'cycle_id': key.cycle_id,
                'opportunity_id': key.opportunity_id,
                'c1_card_id': c1['identity']['card_id'],
                'c2_card_id': c2['identity']['card_id'],
                'c1_decision': 'BUILD_PAYLOAD',
                'c2_decision': c2_decision,
                'simulated_c1_net_usd': c1_net,
                'simulated_c2_net_usd': c2_net,
                'simulated_net_usd': c1_net + c2_net,
                'realized_net_opportunity_usd': None,
                'realized_status': 'DRY_RUN_NO_BROADCAST',
                'cycle_status': 'C1_BUILT_C2_EXECUTE' if c2_decision == 'EXECUTE' else 'C1_BUILT_C2_NO_OP',
            }
            self.log.cycle_pair(pair)
            cycles.append(pair)

        self._write_block_summaries(cycles)
        summary = {'requested_limit': limit, 'completed_cycles': len(cycles), 'dna_cards': len(cycles) * 2}
        Path('logs/dry_run_summary.json').write_text(json.dumps(summary), encoding='utf-8')
        self._emit('DRY_RUN_DONE', summary)
        return summary

    def _write_block_summaries(self, cycles: list[dict]) -> None:
        grouped = {}
        for c in cycles:
            grouped.setdefault(c['block_number'], []).append(c)
        for bn, rows in grouped.items():
            row = {
                'block_number': bn,
                'block_id': f'block_{bn}',
                'block_cycle_count': len(rows),
                'global_cycle_numbers': [r['global_cycle_number'] for r in rows],
                'opportunity_ids': [r['opportunity_id'] for r in rows],
                'block_simulated_net_usd': sum(r['simulated_net_usd'] for r in rows),
                'block_realized_net_opportunity_usd': None,
                'realized_status': 'DRY_RUN_NO_BROADCAST',
            }
            self.log.block_cycle(row)
            self._emit('BLOCK_SUMMARY_UPDATED', {'block_number': bn})

    def _emit(self, event_type: str, payload: dict) -> None:
        event = self.bus.emit(event_type, payload)
        self.log.event(event)
