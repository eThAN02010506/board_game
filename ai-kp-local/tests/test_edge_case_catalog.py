from collections import Counter

from ai_kp.evaluation.edge_case_catalog import EDGE_CASES, validate_edge_case_catalog


def test_long_campaign_edge_catalog_has_fifty_independent_regressions() -> None:
    validate_edge_case_catalog()
    assert len(EDGE_CASES) == 50
    assert len({item.id for item in EDGE_CASES}) == 50
    assert len({item.pytest_node for item in EDGE_CASES}) == 50
    categories = Counter(item.category for item in EDGE_CASES)
    assert categories["safety"] >= 4
    assert categories["privacy"] >= 3
    assert categories["continuity"] >= 4
    assert categories["lifecycle"] >= 2
    assert categories["inventory"] >= 4
    assert categories["combat"] >= 8
    assert categories["parallel"] >= 20
    assert categories["security"] >= 4
    assert categories["weak_model"] >= 1
