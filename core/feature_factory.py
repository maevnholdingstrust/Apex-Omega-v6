from typing import List, Dict, Any
from .types import Feature

def extract_features(data: Dict[str, Any]) -> List[Feature]:
    """Deterministic feature extraction."""
    features = []
    for key, value in data.items():
        if isinstance(value, (int, float)):
            features.append(Feature(name=key, value=float(value)))
    return features