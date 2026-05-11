use apex_engine::{
    build_c1_envelope_from_steps, generate_c2_candidates_from_post_c1, hybrid_buffer,
    optimize_buffer_ev, quote_exact_in, select_best_c2_candidate, simulate_two_leg, AmmError,
    C2Action, PoolFamily, PoolState, RouteEnvelope, RouteStep,
};

fn addr(v: u8) -> [u8; 20] {
    [v; 20]
}

fn pool(family: PoolFamily) -> PoolState {
    PoolState {
        address: addr(1),
        token0: addr(2),
        token1: addr(3),
        reserve0: 1_000_000,
        reserve1: 2_000_000,
        fee_bps: 30,
        family,
        block_number: 100,
        verified: true,
        tvl_usd: 100_000.0,
    }
}

fn step(data: Vec<u8>) -> RouteStep {
    RouteStep {
        protocol: 2,
        target: addr(9),
        approve_token: addr(2),
        output_token: addr(3),
        call_value: 0,
        min_amount_in: 100,
        min_amount_out: 90,
        fee_bps: 30,
        data,
        optional: false,
    }
}

fn envelope() -> RouteEnvelope {
    RouteEnvelope {
        version: 1,
        profit_token: addr(3),
        gas_reserve_asset: 0,
        dex_fee_reserve_asset: 0,
        steps: vec![step(vec![1])],
    }
}

#[test]
fn v2_uses_cpmm_math() {
    let out = quote_exact_in(&pool(PoolFamily::V2_CPMM), true, 10_000).unwrap();
    let amount_in_after_fee = 10_000u128 * 9_970u128 / 10_000u128;
    let expected = amount_in_after_fee * 2_000_000u128 / (1_000_000u128 + amount_in_after_fee);
    assert_eq!(out, expected);
}

#[test]
fn unsupported_families_cannot_use_v2_math() {
    for family in [
        PoolFamily::V3_CLMM,
        PoolFamily::ALGEBRA_CLMM,
        PoolFamily::CURVE_STABLE,
        PoolFamily::BALANCER_WEIGHTED,
        PoolFamily::UNKNOWN,
    ] {
        assert_eq!(
            quote_exact_in(&pool(family), true, 10_000),
            Err(AmmError::UnsupportedPoolFamily)
        );
    }
}

#[test]
fn leg2_input_equals_leg1_output() {
    let p1 = pool(PoolFamily::V2_CPMM);
    let mut p2 = pool(PoolFamily::V2_CPMM);
    p2.reserve0 = 2_000_000;
    p2.reserve1 = 1_100_000;
    let (leg1_out, leg2_out) = simulate_two_leg(&p1, true, &p2, true, 10_000).unwrap();
    assert_eq!(leg2_out, quote_exact_in(&p2, true, leg1_out).unwrap());
}

#[test]
fn hybrid_buffer_clamps_floor_and_cap() {
    let floor = hybrid_buffer(0.001, 1_000.0, 0.0001, 1.0, 0.002, 0.02);
    let cap = hybrid_buffer(1.0, 1_000_000.0, 1.0, 10.0, 0.002, 0.02);
    assert_eq!(floor, 0.002);
    assert_eq!(cap, 0.02);
}

#[test]
fn ev_optimizer_selects_max_ev_member_of_curve() {
    let selection = optimize_buffer_ev(
        0.01,
        100.0,
        5.0,
        |buffer| if buffer >= 0.0125 { 0.95 } else { 0.50 },
        |buffer| buffer * 100.0,
    );
    let best = selection
        .curve
        .iter()
        .max_by(|a, b| a.ev_usdc.total_cmp(&b.ev_usdc))
        .unwrap();
    assert_eq!(selection.selected_buffer, best.buffer);
    assert_eq!(selection.selected_ev_usdc, best.ev_usdc);
}

#[test]
fn c1_rejects_empty_calldata() {
    let result = build_c1_envelope_from_steps(addr(3), vec![step(vec![])]);
    assert!(result.is_err());
}

#[test]
fn c2_always_includes_do_nothing() {
    let candidates = generate_c2_candidates_from_post_c1(None, 0.0, None, 0.0, 1.0);
    assert!(candidates
        .iter()
        .any(|candidate| candidate.action == C2Action::DoNothing));
}

#[test]
fn c2_rejects_mirror_reverse_below_ev_threshold() {
    let candidates =
        generate_c2_candidates_from_post_c1(Some(envelope()), 0.5, Some(envelope()), 0.25, 1.0);
    assert!(candidates
        .iter()
        .filter(|candidate| candidate.action != C2Action::DoNothing)
        .all(|candidate| !candidate.executable));
    assert_eq!(
        select_best_c2_candidate(&candidates).action,
        C2Action::DoNothing
    );
}

#[test]
fn c2_selects_highest_positive_ev_candidate() {
    let candidates =
        generate_c2_candidates_from_post_c1(Some(envelope()), 5.0, Some(envelope()), 9.0, 1.0);
    let selected = select_best_c2_candidate(&candidates);
    assert_eq!(selected.action, C2Action::Reverse);
    assert_eq!(selected.expected_ev_usd, 9.0);
}
