"""Tests for the C2 candidate-payload engine."""

import pytest

from apex_omega_core.strategies.payload_engine import (
    CandidateSelector,
    PayloadCandidate,
    ValidityWindow,
)


# ---------------------------------------------------------------------------
# ValidityWindow
# ---------------------------------------------------------------------------

class TestValidityWindow:
    def test_is_valid_at_start_block(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.is_valid_at(100) is True

    def test_is_valid_at_end_block(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.is_valid_at(105) is True

    def test_is_valid_at_middle_block(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.is_valid_at(102) is True

    def test_invalid_before_window(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.is_valid_at(99) is False

    def test_invalid_after_window(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.is_valid_at(106) is False

    def test_blocks_remaining_inside(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.blocks_remaining(102) == 3

    def test_blocks_remaining_at_end(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.blocks_remaining(105) == 0

    def test_blocks_remaining_expired(self):
        vw = ValidityWindow(start_block=100, end_block=105)
        assert vw.blocks_remaining(110) == 0


# ---------------------------------------------------------------------------
# PayloadCandidate
# ---------------------------------------------------------------------------

def _make_candidate(
    base_ev: float = 100.0,
    decay_rate: float = 0.05,
    risk_penalty: float = 10.0,
    min_profit: float = 5.0,
    current_block: int = 200,
) -> PayloadCandidate:
    return PayloadCandidate(
        name="forward_strike",
        route_plan=[{"venue": "uniswap_v2"}],
        validity=ValidityWindow(start_block=current_block, end_block=current_block + 5),
        base_ev=base_ev,
        decay_rate=decay_rate,
        risk_penalty=risk_penalty,
        min_profit=min_profit,
        size_hint_usd=50_000.0,
        route_kind="forward",
    )


class TestPayloadCandidate:
    def test_risk_adjusted_ev_at_zero_offset(self):
        c = _make_candidate(base_ev=100.0, decay_rate=0.05, risk_penalty=10.0)
        assert c.risk_adjusted_ev(0) == pytest.approx(90.0)

    def test_risk_adjusted_ev_decays_per_block(self):
        c = _make_candidate(base_ev=100.0, decay_rate=0.10, risk_penalty=0.0)
        # After 1 block: 100 * 0.9 = 90
        assert c.risk_adjusted_ev(1) == pytest.approx(90.0)
        # After 2 blocks: 100 * 0.81 = 81
        assert c.risk_adjusted_ev(2) == pytest.approx(81.0)

    def test_risk_adjusted_ev_never_negative_base_ev(self):
        c = _make_candidate(base_ev=100.0, decay_rate=1.0, risk_penalty=0.0)
        # decay_rate=1.0 -> factor=0 -> EV=0, penalty=0
        assert c.risk_adjusted_ev(1) == pytest.approx(0.0)

    def test_is_profitable_at_zero_offset(self):
        c = _make_candidate(base_ev=100.0, risk_penalty=10.0, min_profit=5.0)
        assert c.is_profitable_at(0) is True

    def test_is_not_profitable_when_ev_below_min(self):
        # base_ev=10, risk_penalty=8, min_profit=5 -> ev=2, not > 5
        c = _make_candidate(base_ev=10.0, risk_penalty=8.0, min_profit=5.0)
        assert c.is_profitable_at(0) is False

    def test_candidate_id_unique_by_default(self):
        c1 = _make_candidate()
        c2 = _make_candidate()
        assert c1.candidate_id != c2.candidate_id

    def test_candidate_id_can_be_set(self):
        import dataclasses
        c = dataclasses.replace(_make_candidate(), candidate_id="fixed-id")
        assert c.candidate_id == "fixed-id"

    def test_route_plan_preserved(self):
        route = [{"venue": "balancer", "fee": 0.003}]
        c = PayloadCandidate(
            name="test",
            route_plan=route,
            validity=ValidityWindow(start_block=1, end_block=6),
            base_ev=50.0,
            decay_rate=0.05,
            risk_penalty=5.0,
            min_profit=2.0,
            size_hint_usd=10_000.0,
            route_kind="forward",
        )
        assert c.route_plan is route


# ---------------------------------------------------------------------------
# CandidateSelector
# ---------------------------------------------------------------------------

def _sample_route():
    return [
        {
            'venue': 'uniswap_v2',
            'reserve_in': 1_000_000.0,
            'reserve_out': 1_000_000.0,
            'fee': 0.003,
        }
    ]


def _sample_sentinel_output(profit: float = 50.0, optimal_input: float = 10_000.0) -> dict:
    return {
        'profit': profit,
        'optimal_input': optimal_input,
        'final_output': optimal_input + profit,
        'slippage_per_leg': [{'slippage': 0.001}],
    }


class TestCandidateSelector:
    def test_build_candidates_forward_only_when_no_reverse(self):
        sel = CandidateSelector()
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=50.0),
            gas_cost=5.0,
            current_block=100,
        )
        assert len(candidates) == 1
        assert candidates[0].route_kind == "forward"
        assert candidates[0].name == "forward_strike"

    def test_build_candidates_with_reverse(self):
        sel = CandidateSelector()
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=50.0),
            gas_cost=5.0,
            current_block=100,
            reverse_route=list(reversed(_sample_route())),
            reverse_output=_sample_sentinel_output(profit=30.0),
        )
        route_kinds = {c.route_kind for c in candidates}
        assert "forward" in route_kinds
        assert "reverse" in route_kinds

    def test_build_candidates_includes_duplicate_when_high_ev(self):
        # profit=50, gas_cost=5 → profit > gas_cost * 2=10 → duplicate included
        sel = CandidateSelector()
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=50.0),
            gas_cost=5.0,
            current_block=100,
            reverse_route=list(reversed(_sample_route())),
            reverse_output=_sample_sentinel_output(profit=30.0),
        )
        route_kinds = {c.route_kind for c in candidates}
        assert "duplicate" in route_kinds

    def test_build_candidates_excludes_duplicate_when_low_ev(self):
        # profit=8, gas_cost=5 → profit < gas_cost * 2=10 → no duplicate
        sel = CandidateSelector()
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=8.0),
            gas_cost=5.0,
            current_block=100,
            reverse_route=list(reversed(_sample_route())),
            reverse_output=_sample_sentinel_output(profit=3.0),
        )
        route_kinds = {c.route_kind for c in candidates}
        assert "duplicate" not in route_kinds

    def test_build_candidates_no_forward_when_profit_zero(self):
        sel = CandidateSelector()
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=0.0),
            gas_cost=5.0,
            current_block=100,
        )
        assert len(candidates) == 0

    def test_build_candidates_validity_window_correct(self):
        sel = CandidateSelector(window_blocks=3)
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=50.0),
            gas_cost=5.0,
            current_block=200,
        )
        assert candidates[0].validity.start_block == 200
        assert candidates[0].validity.end_block == 203

    def test_revalidate_drops_expired_candidates(self):
        sel = CandidateSelector(window_blocks=5)
        c = _make_candidate(current_block=100)
        # Expired: current_block=106 > end_block=105
        surviving = sel.revalidate([c], current_block=106, creation_block=100)
        assert surviving == []

    def test_revalidate_drops_unprofitable_candidates(self):
        sel = CandidateSelector()
        # High decay, so by block_offset=3 it will be unprofitable
        c = _make_candidate(base_ev=15.0, decay_rate=0.5, risk_penalty=10.0, min_profit=5.0, current_block=100)
        # offset=3: ev = 15 * (0.5^3) - 10 = 15*0.125 - 10 = 1.875 - 10 = -8.125 ≤ 5.0
        surviving = sel.revalidate([c], current_block=103, creation_block=100)
        assert surviving == []

    def test_revalidate_keeps_valid_profitable_candidates(self):
        sel = CandidateSelector()
        c = _make_candidate(base_ev=100.0, decay_rate=0.05, risk_penalty=5.0, min_profit=5.0, current_block=100)
        # offset=2: ev = 100*(0.95^2) - 5 = 90.25 - 5 = 85.25 > 5
        surviving = sel.revalidate([c], current_block=102, creation_block=100)
        assert len(surviving) == 1

    def test_select_best_returns_highest_ev_candidate(self):
        sel = CandidateSelector()
        import dataclasses
        c_low = dataclasses.replace(
            _make_candidate(base_ev=30.0, risk_penalty=5.0, current_block=100),
            candidate_id="low",
            name="low_ev",
        )
        c_high = dataclasses.replace(
            _make_candidate(base_ev=100.0, risk_penalty=5.0, current_block=100),
            candidate_id="high",
            name="high_ev",
        )
        best = sel.select_best([c_low, c_high], current_block=100, creation_block=100)
        assert best is not None
        assert best.candidate_id == "high"

    def test_select_best_returns_none_when_all_expired(self):
        sel = CandidateSelector(window_blocks=2)
        c = _make_candidate(current_block=100)
        # c.validity.end_block = 102; current_block=110 → expired
        best = sel.select_best([c], current_block=110, creation_block=100)
        assert best is None

    def test_select_best_returns_none_when_empty(self):
        sel = CandidateSelector()
        assert sel.select_best([], current_block=100, creation_block=100) is None

    def test_custom_window_blocks(self):
        sel = CandidateSelector(window_blocks=10)
        candidates = sel.build_candidates(
            route=_sample_route(),
            sentinel_output=_sample_sentinel_output(profit=50.0),
            gas_cost=5.0,
            current_block=300,
        )
        assert candidates[0].validity.end_block == 310


# ---------------------------------------------------------------------------
# C2SurgeonApex integration
# ---------------------------------------------------------------------------

class TestC2SurgeonApexIntegration:
    """Verify that decide_contract_action populates winning_candidate / candidates."""

    def _make_route(self):
        return [
            {
                'venue': 'uniswap_v2',
                'reserve_in': 1_000_000.0,
                'reserve_out': 1_000_000.0,
                'fee': 0.003,
                'price_in_usd': 1.0,
                'price_out_usd': 1.05,
                'tvl_usd': 2_000_000.0,
                'volume_24h_usd': 500_000.0,
                'age_in_blocks': 100.0,
            },
            {
                'venue': 'sushiswap',
                'reserve_in': 1_000_000.0,
                'reserve_out': 1_000_000.0,
                'fee': 0.003,
                'price_in_usd': 1.05,
                'price_out_usd': 1.0,
                'tvl_usd': 2_000_000.0,
                'volume_24h_usd': 400_000.0,
                'age_in_blocks': 100.0,
            },
        ]

    def test_decide_contract_action_includes_candidates_key(self):
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        surgeon = C2SurgeonApex()
        result = surgeon.decide_contract_action(
            route=self._make_route(),
            raw_spread=0.05,
            min_input=1000.0,
            max_input=50_000.0,
            gas_cost=5.0,
            current_block=500,
        )
        assert 'candidates' in result
        assert isinstance(result['candidates'], list)

    def test_decide_contract_action_includes_winning_candidate_key(self):
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        surgeon = C2SurgeonApex()
        result = surgeon.decide_contract_action(
            route=self._make_route(),
            raw_spread=0.05,
            min_input=1000.0,
            max_input=50_000.0,
            gas_cost=5.0,
            current_block=500,
        )
        assert 'winning_candidate' in result

    def test_winning_candidate_is_none_for_do_nothing(self):
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        surgeon = C2SurgeonApex()
        # Zero spread → net_profit ≤ 0 → DO_NOTHING
        result = surgeon.decide_contract_action(
            route=self._make_route(),
            raw_spread=0.0,
            min_input=1000.0,
            max_input=50_000.0,
            gas_cost=5.0,
            current_block=500,
        )
        assert result['decision'] == 'DO_NOTHING'
        assert result['winning_candidate'] is None

    def test_existing_keys_still_present(self):
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        surgeon = C2SurgeonApex()
        result = surgeon.decide_contract_action(
            route=self._make_route(),
            raw_spread=0.05,
            min_input=1000.0,
            max_input=50_000.0,
            gas_cost=5.0,
        )
        for key in ('decision', 'sentinel_output', 'fork_validation', 'mempool_validation', 'target_address'):
            assert key in result
