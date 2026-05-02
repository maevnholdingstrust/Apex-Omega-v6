import json
from pathlib import Path


class DnaLogger:
    def __init__(self, base: str = 'logs'):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, row: dict) -> None:
        with (self.base / name).open('a', encoding='utf-8') as f:
            f.write(json.dumps(row) + '\n')

    def dna_card(self, row: dict) -> None:
        self._append('dry_run_dna_cards.jsonl', row)

    def cycle_pair(self, row: dict) -> None:
        self._append('dry_run_cycle_pairs.jsonl', row)

    def block_cycle(self, row: dict) -> None:
        self._append('dry_run_block_cycles.jsonl', row)

    def payload(self, row: dict) -> None:
        self._append('dry_run_payload_builds.jsonl', row)

    def rejection(self, row: dict) -> None:
        self._append('dry_run_rejections.jsonl', row)

    def event(self, row: dict) -> None:
        self._append('dry_run_dashboard_events.jsonl', row)
