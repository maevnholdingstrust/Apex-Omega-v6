from typing import List
from .types import InferenceResult, Feature

def derive_net_edge(data: dict) -> InferenceResult:
    """Net edge derivation with no double-count."""
    # Inference logic
    net_edge = data.get('edge', 0.0)
    features = [
        Feature(name='edge', value=net_edge),
        Feature(name='confidence', value=0.95)
    ]
    return InferenceResult(net_edge=net_edge, features=features)