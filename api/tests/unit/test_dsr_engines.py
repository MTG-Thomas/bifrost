from src.services.dsr_engines import classify_risk


def test_classify_risk_high_for_destroy() -> None:
    risk, approval = classify_risk({"action": "destroy", "changes": ["delete subnet"]})
    assert risk == "high"
    assert approval is True


def test_classify_risk_medium_for_network_modify() -> None:
    risk, approval = classify_risk({"action": "modify", "changes": ["network acl"]})
    assert risk == "medium"
    assert approval is True


def test_classify_risk_low_for_create() -> None:
    risk, approval = classify_risk({"action": "create", "changes": ["instance"]})
    assert risk == "low"
    assert approval is False
