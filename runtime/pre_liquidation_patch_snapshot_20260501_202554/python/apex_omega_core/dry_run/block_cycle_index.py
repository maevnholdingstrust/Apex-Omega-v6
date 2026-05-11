"""
Block Cycle Index

Provides block-level cycle tracking with monotonic global cycle numbers.

Classes:
    BlockCycleIndex: Main block cycle tracking class
    get_block_cycle_index: Singleton accessor
"""

import threading
from typing import Optional


class BlockCycleIndex:
    """
    Tracks block-level cycles with monotonic global cycle numbers.
    
    Hierarchy:
        block_number
            ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ block_cycle_number (1, 2, 3, ...)
            ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ global_cycle_number (monotonic across all blocks)
    """
    
    def __init__(self):
        """Initialize block cycle index."""
        self._lock = threading.RLock()
        
        # Current state
        self._current_block: Optional[int] = None
        self._block_cycle_number: int = 0
        self._global_cycle_number: int = 0
        
        # Per-block tracking
        self._block_to_first_global: dict[int, int] = {}
        self._block_cycle_counts: dict[int, int] = {}
        
        # History
        self._completed_blocks: dict[int, dict] = {}
    
    def start_block(self, block_number: int) -> None:
        """
        Start tracking a new block.
        
        Args:
            block_number: The block number to start.
        """
        with self._lock:
            if self._current_block != block_number:
                # Save previous block state if exists
                if self._current_block is not None:
                    self._completed_blocks[self._current_block] = {
                        "block_number": self._current_block,
                        "block_cycle_count": self._block_cycle_number,
                        "global_cycle_range": (
                            self._block_to_first_global.get(self._current_block, 0),
                            self._global_cycle_number
                        ),
                    }
                
                # Start new block
                self._current_block = block_number
                self._block_cycle_number = 0
    
    def next_cycle(self, block_number: int) -> dict:
        """
        Get the next cycle identifiers for a block.
        
        Args:
            block_number: The block number for this cycle.
        
        Returns:
            Dictionary with cycle identifiers:
            - block_number
            - block_cycle_number
            - global_cycle_number
            - block_id
            - cycle_id
            - opportunity_id
            - c1_card_id
            - c2_card_id
        """
        with self._lock:
            # Start new block if needed
            if self._current_block != block_number:
                self.start_block(block_number)
            
            # Increment cycle numbers
            self._block_cycle_number += 1
            self._global_cycle_number += 1
            
            # Record first global for this block
            if block_number not in self._block_to_first_global:
                self._block_to_first_global[block_number] = self._global_cycle_number
            
            # Update block cycle count
            self._block_cycle_counts[block_number] = self._block_cycle_number
            
            # Build identifiers
            block_id = f"block_{block_number}"
            cycle_id = f"block_{block_number}_cycle_{self._block_cycle_number:06d}_global_{self._global_cycle_number:06d}"
            opportunity_id = f"opportunity_{self._global_cycle_number:06d}"
            c1_card_id = f"{opportunity_id}_c1"
            c2_card_id = f"{opportunity_id}_c2"
            
            return {
                "block_number": block_number,
                "block_cycle_number": self._block_cycle_number,
                "global_cycle_number": self._global_cycle_number,
                "block_id": block_id,
                "cycle_id": cycle_id,
                "opportunity_id": opportunity_id,
                "c1_card_id": c1_card_id,
                "c2_card_id": c2_card_id,
            }
    
    def get_block_summary(self, block_number: int) -> Optional[dict]:
        """
        Get summary for a specific block.
        
        Args:
            block_number: The block number to summarize.
        
        Returns:
            Dictionary with block summary or None if not found.
        """
        with self._lock:
            if block_number in self._completed_blocks:
                return self._completed_blocks[block_number]
            
            if block_number == self._current_block:
                return {
                    "block_number": block_number,
                    "block_cycle_count": self._block_cycle_number,
                    "global_cycle_range": (
                        self._block_to_first_global.get(block_number, 0),
                        self._global_cycle_number
                    ),
                }
            
            return None
    
    def get_all_block_summaries(self) -> list[dict]:
        """
        Get summaries for all blocks.
        
        Returns:
            List of block summaries.
        """
        with self._lock:
            summaries = []
            
            # Completed blocks
            for bn, data in self._completed_blocks.items():
                summaries.append(data)
            
            # Current block
            if self._current_block is not None:
                summaries.append({
                    "block_number": self._current_block,
                    "block_cycle_count": self._block_cycle_number,
                    "global_cycle_range": (
                        self._block_to_first_global.get(self._current_block, 0),
                        self._global_cycle_number
                    ),
                })
            
            return sorted(summaries, key=lambda x: x["block_number"])
    
    def get_current_state(self) -> dict:
        """
        Get current index state.
        
        Returns:
            Dictionary with current state.
        """
        with self._lock:
            return {
                "current_block": self._current_block,
                "block_cycle_number": self._block_cycle_number,
                "global_cycle_number": self._global_cycle_number,
                "blocks_tracked": len(self._block_to_first_global),
            }
    
    def reset(self) -> None:
        """Reset the index."""
        with self._lock:
            self._current_block = None
            self._block_cycle_number = 0
            self._global_cycle_number = 0
            self._block_to_first_global.clear()
            self._block_cycle_counts.clear()
            self._completed_blocks.clear()


# Singleton instance
_block_cycle_index: Optional[BlockCycleIndex] = None
_index_lock = threading.Lock()


def get_block_cycle_index(log_dir=None) -> BlockCycleIndex:
    """
    Get singleton BlockCycleIndex instance. log_dir is accepted for runner compatibility.
    
    Returns:
        BlockCycleIndex singleton instance.
    """
    global _block_cycle_index
    
    with _index_lock:
        if _block_cycle_index is None:
            _block_cycle_index = BlockCycleIndex()
        return _block_cycle_index


def reset_block_cycle_index() -> None:
    """Reset the singleton index."""
    global _block_cycle_index
    
    with _index_lock:
        if _block_cycle_index:
            _block_cycle_index.reset()
        _block_cycle_index = None

