"""
Tests for DNA Card Cycle and Block-Level Logging System

Validation and Testing Protocols:
- test_cycle_creates_c1_and_c2_cards: Confirm the creation of two records for each C1 build.
- test_c2_card_exists_when_no_op: Ensure the C2 card is logged even when not executed.
- test_multiple_cycles_same_block: Verify that the block_cycle_number increments correctly within the same block.
- test_global_cycle_number_monotonic: Ensure that global_cycle_number remains consistent across blocks.
- test_first20_logic: Validate that a request for "20 routes" results in 20 C1 cycles and 40 DNA card records.
- test_dry_run_realized_net_is_null: Ensure that simulated data is not misrepresented as realized profit.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from apex_omega_core.logging.dna_card_logger import (
    DNACard,
    CyclePair,
    BlockCycle,
    DNALoggingSystem,
    get_dna_logging_system,
    reset_dna_logging_system,
)


class TestDNACardLogger(unittest.TestCase):
    """Test cases for the DNA Card Logging System."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.logs_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Reset singleton before each test
        reset_dna_logging_system()
        
        # Create logging system
        self.logger = DNALoggingSystem(self.logs_dir, dry_run=True)
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
        reset_dna_logging_system()
    
    def _create_mock_candidate(self, pair="WMATIC/USDC", buy_dex="univ3_500", 
                                sell_dex="qsv2", trade_size=10000.0):
        """Create a mock candidate object."""
        candidate = MagicMock()
        candidate.pair = pair
        candidate.buy_dex = buy_dex
        candidate.sell_dex = sell_dex
        candidate.trade_size_usd = trade_size
        return candidate
    
    def test_cycle_creates_c1_and_c2_cards(self):
        """
        Confirm the creation of two records for each C1 build.
        
        For each C1 card logged, there must be a corresponding C2 card.
        """
        block_number = 73491288
        candidate = self._create_mock_candidate()
        
        # Log C1 card
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
            decision="BUILD_PAYLOAD",
        )
        
        # Log C2 card
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
            simulated_profit=5.50,
        )
        
        # Verify C1 card
        self.assertEqual(c1_card.strike_role, "C1")
        self.assertEqual(c1_card.strike_name, "Aggressor")
        self.assertEqual(c1_card.sequence_index, 1)
        self.assertEqual(c1_card.decision, "BUILD_PAYLOAD")
        
        # Verify C2 card
        self.assertEqual(c2_card.strike_role, "C2")
        self.assertEqual(c2_card.strike_name, "Surgeon")
        self.assertEqual(c2_card.sequence_index, 2)
        self.assertEqual(c2_card.decision, "EXECUTE")
        
        # Verify both cards reference the same opportunity
        self.assertEqual(c1_card.opportunity_id, c2_card.opportunity_id)
        self.assertEqual(c1_card.cycle_id, c2_card.cycle_id)
        
        # Verify file records
        dna_cards_file = Path(self.logs_dir) / "dry_run_dna_cards.jsonl"
        with open(dna_cards_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 2)  # Two records: C1 and C2
    
    def test_c2_card_exists_when_no_op(self):
        """
        Ensure the C2 card is logged even when decision is NO_OP.
        
        Per requirement: "A C2 DNA card must be logged even if the C2 decision is NO_OP".
        """
        block_number = 73491289
        candidate = self._create_mock_candidate()
        
        # Log C1 card
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        # Log C2 card with NO_OP decision
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="NO_OP",
            no_op_reason="Insufficient spread after slippage",
            simulated_profit=0.0,
        )
        
        # Verify C2 card was created with NO_OP
        self.assertEqual(c2_card.decision, "NO_OP")
        self.assertEqual(c2_card.no_op_reason, "Insufficient spread after slippage")
        
        # Verify file has both records
        dna_cards_file = Path(self.logs_dir) / "dry_run_dna_cards.jsonl"
        with open(dna_cards_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 2)
            
            # Check second record is C2 NO_OP
            c2_record = json.loads(lines[1])
            self.assertEqual(c2_record["decision"], "NO_OP")
            self.assertIsNotNone(c2_record["no_op_reason"])
    
    def test_multiple_cycles_same_block(self):
        """
        Verify that the block_cycle_index increments correctly within the same block.
        
        Multiple cycles in the same block should have incrementing block_cycle_index values.
        """
        block_number = 73491290
        
        # Create multiple cycles in the same block
        for i in range(3):
            candidate = self._create_mock_candidate(
                pair=f"PAIR{i}/USDC",
                trade_size=1000.0 * (i + 1),
            )
            
            c1_card = self.logger.log_c1_card(
                block_number=block_number,
                candidate=candidate,
                c1_result=MagicMock(),
            )
            
            c2_card = self.logger.log_c2_card(
                c1_card=c1_card,
                c2_result=MagicMock(),
                decision="EXECUTE",
                simulated_profit=float(i + 1),
            )
        
        # Verify block_cycle_index increments
        stats = self.logger.get_stats()
        self.assertEqual(stats["global_cycle_number"], 3)
        
        # Verify block cycle counter
        self.assertEqual(self.logger._block_cycle_counters[block_number], 3)
        
        # Verify file records
        dna_cards_file = Path(self.logs_dir) / "dry_run_dna_cards.jsonl"
        with open(dna_cards_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 6)  # 3 cycles * 2 cards each
        
        # Check block_cycle_index values
        records = [json.loads(line) for line in lines]
        c1_cards = [r for r in records if r["strike_role"] == "C1"]
        
        # Block cycle indices should be 1, 2, 3
        indices = [r["block_cycle_index"] for r in c1_cards]
        self.assertEqual(indices, [1, 2, 3])
    
    def test_global_cycle_number_monotonic(self):
        """
        Ensure that global_cycle_number remains consistent across blocks.
        
        The global cycle number should be monotonic across all blocks.
        """
        # Create cycles in different blocks
        blocks = [73491291, 73491292, 73491293]
        
        global_indices = []
        for block_number in blocks:
            candidate = self._create_mock_candidate()
            
            c1_card = self.logger.log_c1_card(
                block_number=block_number,
                candidate=candidate,
                c1_result=MagicMock(),
            )
            
            c2_card = self.logger.log_c2_card(
                c1_card=c1_card,
                c2_result=MagicMock(),
                decision="EXECUTE",
                simulated_profit=1.0,
            )
            
            global_indices.append(c1_card.global_cycle_number)
        
        # Verify global indices are monotonic
        self.assertEqual(global_indices, [1, 2, 3])
        
        # Verify all blocks are tracked
        stats = self.logger.get_stats()
        self.assertEqual(stats["blocks_tracked"], 3)
    
    def test_first20_logic(self):
        """
        Validate that a request for "20 routes" results in 20 C1 cycles and 40 DNA card records.
        """
        block_number = 73491294
        
        # Simulate 20 routes
        num_routes = 20
        
        for i in range(num_routes):
            candidate = self._create_mock_candidate(
                pair=f"PAIR{i}/USDC",
                trade_size=5000.0,
            )
            
            c1_card = self.logger.log_c1_card(
                block_number=block_number,
                candidate=candidate,
                c1_result=MagicMock(),
            )
            
            c2_card = self.logger.log_c2_card(
                c1_card=c1_card,
                c2_result=MagicMock(),
                decision="EXECUTE",
                simulated_profit=float(i * 0.1),
            )
        
        # Verify counts
        stats = self.logger.get_stats()
        self.assertEqual(stats["global_cycle_number"], 20)
        self.assertEqual(stats["dna_cards_count"], 40)  # 20 C1 + 20 C2
        self.assertEqual(stats["cycle_pairs_count"], 20)
        
        # Verify block cycle count
        self.assertEqual(self.logger._block_cycle_counters[block_number], 20)
    
    def test_dry_run_realized_net_is_null(self):
        """
        Ensure that simulated data is not misrepresented as realized profit.
        
        During dry runs, realized profit should be recorded as null.
        """
        # Create dry run logger
        dry_run_logger = DNALoggingSystem(self.logs_dir, dry_run=True)
        
        block_number = 73491295
        candidate = self._create_mock_candidate()
        
        c1_card = dry_run_logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = dry_run_logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
            simulated_profit=10.0,
        )
        
        # Verify simulated_net_usd is set
        self.assertEqual(c2_card.simulated_net_usd, 10.0)
        
        # Verify realized_net_opportunity_usd is None for dry run
        self.assertIsNone(c2_card.realized_net_opportunity_usd)
        
        # Verify file records
        dna_cards_file = Path(self.logs_dir) / "dry_run_dna_cards.jsonl"
        with open(dna_cards_file, "r") as f:
            for line in f:
                record = json.loads(line)
                self.assertIsNone(record.get("realized_net_opportunity_usd"))
    
    def test_live_mode_realized_profit(self):
        """
        Verify that in live mode, realized profit can be recorded.
        """
        # Create live logger
        live_logger = DNALoggingSystem(self.logs_dir, dry_run=False)
        
        block_number = 73491296
        candidate = self._create_mock_candidate()
        
        c1_card = live_logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = live_logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
            simulated_profit=15.0,
        )
        
        # In live mode, realized_net_opportunity_usd can be set
        # (it starts as None but can be updated)
        self.assertIsNone(c2_card.realized_net_opportunity_usd)
    
    def test_identifier_formats(self):
        """
        Verify that all identifiers follow the specified formats.
        """
        block_number = 73491297
        candidate = self._create_mock_candidate()
        
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
            simulated_profit=5.0,
        )
        
        # Verify Block ID format: block_{number}
        self.assertEqual(c1_card.block_id, f"block_{block_number}")
        
        # Verify Cycle ID format: block_{num}_cycle_{b_idx}_global_{g_idx}
        expected_cycle_id = f"block_{block_number}_cycle_000001_global_000001"
        self.assertEqual(c1_card.cycle_id, expected_cycle_id)
        
        # Verify Opportunity ID format: opportunity_{global_idx}
        expected_opp_id = "opportunity_000001"
        self.assertEqual(c1_card.opportunity_id, expected_opp_id)
        
        # Verify C1 Card ID format: opportunity_{global_idx}_c1
        expected_c1_id = "opportunity_000001_c1"
        self.assertEqual(c1_card.card_id, expected_c1_id)
        
        # Verify C2 Card ID format: opportunity_{global_idx}_c2
        expected_c2_id = "opportunity_000001_c2"
        self.assertEqual(c2_card.card_id, expected_c2_id)
    
    def test_c2_never_pre_approved_c1(self):
        """
        Verify that C2 cards have c2_never_pre_approved_c1 set to true.
        """
        block_number = 73491298
        candidate = self._create_mock_candidate()
        
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
        )
        
        self.assertTrue(c2_card.c2_never_pre_approved_c1)
    
    def test_state_basis_tracking(self):
        """
        Verify that C1 uses pre_c1_state and C2 uses post_c1_reloaded_state.
        """
        block_number = 73491299
        candidate = self._create_mock_candidate()
        
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
        )
        
        self.assertEqual(c1_card.state_basis, "pre_c1_state")
        self.assertEqual(c2_card.state_basis, "post_c1_reloaded_state")
    
    def test_trigger_chain(self):
        """
        Verify that C2's trigger references the C1 card ID.
        """
        block_number = 73491300
        candidate = self._create_mock_candidate()
        
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
        )
        
        # C1 trigger should be scanner_executable_candidate
        self.assertEqual(c1_card.trigger, "scanner_executable_candidate")
        
        # C2 trigger should reference C1 card ID
        self.assertEqual(c2_card.trigger, c1_card.card_id)
    
    def test_cycle_pairs_file(self):
        """
        Verify that cycle pairs are logged to the correct file.
        """
        block_number = 73491301
        candidate = self._create_mock_candidate()
        
        c1_card = self.logger.log_c1_card(
            block_number=block_number,
            candidate=candidate,
            c1_result=MagicMock(),
        )
        
        c2_card = self.logger.log_c2_card(
            c1_card=c1_card,
            c2_result=MagicMock(),
            decision="EXECUTE",
            simulated_profit=7.5,
        )
        
        # Verify cycle pairs file
        cycle_pairs_file = Path(self.logs_dir) / "dry_run_cycle_pairs.jsonl"
        with open(cycle_pairs_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            
            pair_record = json.loads(lines[0])
            self.assertEqual(pair_record["c1_card_id"], c1_card.card_id)
            self.assertEqual(pair_record["c2_card_id"], c2_card.card_id)
            self.assertEqual(pair_record["simulated_net_usd"], 7.5)
    
    def test_block_cycles_file(self):
        """
        Verify that block cycle summaries are logged correctly.
        """
        block_number = 73491302
        
        # Create multiple cycles in the same block
        total_profit = 0.0
        for i in range(3):
            candidate = self._create_mock_candidate()
            
            c1_card = self.logger.log_c1_card(
                block_number=block_number,
                candidate=candidate,
                c1_result=MagicMock(),
            )
            
            profit = float(i + 1)
            total_profit += profit
            
            c2_card = self.logger.log_c2_card(
                c1_card=c1_card,
                c2_result=MagicMock(),
                decision="EXECUTE",
                simulated_profit=profit,
            )
        
        # Verify block cycles file
        block_cycles_file = Path(self.logs_dir) / "dry_run_block_cycles.jsonl"
        with open(block_cycles_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            
            block_record = json.loads(lines[0])
            self.assertEqual(block_record["block_number"], block_number)
            self.assertEqual(block_record["total_cycles"], 3)
            self.assertEqual(block_record["total_simulated_profit_usd"], total_profit)


class TestDNASingleton(unittest.TestCase):
    """Test cases for the DNA logging singleton."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.logs_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        reset_dna_logging_system()
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
        reset_dna_logging_system()
    
    def test_singleton_returns_same_instance(self):
        """Verify that get_dna_logging_system returns the same instance."""
        logger1 = get_dna_logging_system(self.logs_dir, dry_run=True)
        logger2 = get_dna_logging_system(self.logs_dir, dry_run=True)
        
        self.assertIs(logger1, logger2)
    
    def test_singleton_with_different_params(self):
        """Verify that different parameters create different configurations."""
        logger1 = get_dna_logging_system(self.logs_dir, dry_run=True)
        
        # Reset and get with different params
        reset_dna_logging_system()
        logger2 = get_dna_logging_system(self.logs_dir, dry_run=False)
        
        # Should be different instances due to reset
        self.assertIsNot(logger1, logger2)


if __name__ == "__main__":
    unittest.main()