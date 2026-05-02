use crate::types::{C2Action, C2Candidate, RouteEnvelope};

pub fn generate_c2_candidates_from_post_c1(
    mirror_route: Option<RouteEnvelope>,
    mirror_ev_usd: f64,
    reverse_route: Option<RouteEnvelope>,
    reverse_ev_usd: f64,
    min_ev_usd: f64,
) -> Vec<C2Candidate> {
    let mut out = Vec::new();
    if let Some(route) = mirror_route {
        out.push(C2Candidate {
            action: C2Action::Mirror,
            route: Some(route),
            selected_buffer: 0.0,
            expected_ev_usd: mirror_ev_usd,
            merkle_leaf: None,
            executable: mirror_ev_usd > min_ev_usd,
        });
    }
    if let Some(route) = reverse_route {
        out.push(C2Candidate {
            action: C2Action::Reverse,
            route: Some(route),
            selected_buffer: 0.0,
            expected_ev_usd: reverse_ev_usd,
            merkle_leaf: None,
            executable: reverse_ev_usd > min_ev_usd,
        });
    }
    out.push(C2Candidate {
        action: C2Action::DoNothing,
        route: None,
        selected_buffer: 0.0,
        expected_ev_usd: 0.0,
        merkle_leaf: None,
        executable: true,
    });
    out
}

pub fn select_best_c2_candidate(candidates: &[C2Candidate]) -> C2Candidate {
    candidates
        .iter()
        .filter(|candidate| candidate.executable && candidate.action != C2Action::DoNothing)
        .max_by(|a, b| a.expected_ev_usd.total_cmp(&b.expected_ev_usd))
        .cloned()
        .unwrap_or_else(|| {
            candidates
                .iter()
                .find(|candidate| candidate.action == C2Action::DoNothing)
                .cloned()
                .unwrap_or(C2Candidate {
                    action: C2Action::DoNothing,
                    route: None,
                    selected_buffer: 0.0,
                    expected_ev_usd: 0.0,
                    merkle_leaf: None,
                    executable: true,
                })
        })
}
