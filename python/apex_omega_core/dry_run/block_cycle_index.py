from dataclasses import dataclass


@dataclass
class CycleKey:
    block_number: int
    block_cycle_number: int
    global_cycle_number: int

    @property
    def block_id(self) -> str:
        return f"block_{self.block_number}"

    @property
    def cycle_id(self) -> str:
        return f"block_{self.block_number}_cycle_{self.block_cycle_number:06d}_global_{self.global_cycle_number:06d}"

    @property
    def opportunity_id(self) -> str:
        return f"opportunity_{self.global_cycle_number:06d}"


class BlockCycleIndex:
    def __init__(self) -> None:
        self._global = 0
        self._per_block = {}

    def next(self, block_number: int) -> CycleKey:
        self._global += 1
        self._per_block[block_number] = self._per_block.get(block_number, 0) + 1
        return CycleKey(block_number, self._per_block[block_number], self._global)
