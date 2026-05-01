import pytest
from apex_omega_core.core.feature_factory import extract_features
from apex_omega_core.core.types import Feature

def test_extract_features():
    data = {'price': 100.0, 'volume': 1000}
    features = extract_features(data)
    assert len(features) == 2
    assert features[0].name == 'price'
    assert features[0].value == 100.0