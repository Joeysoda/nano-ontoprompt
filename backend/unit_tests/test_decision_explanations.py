from app.services.v2.decision_explanations import build_evidence, verify

CANDIDATE = {"id": "c", "label": "Expedite", "quarter_revenue_after": 1385000.5, "quarter_revenue_delta": 198286.78, "otif_after": 0.563,
             "at_risk_after": 59, "incremental_cost": 22715.55, "protected_orders": [{"demand_id": "D1"}], "harmed_orders": [], "actions": []}
OUTPUTS = {"totals": {"projected_revenue": 1186706.62, "revenue_target": 1857023.73, "otif": 0.4889, "at_risk": 69}, "coverage": {"rows": []}}


def test_numbers_must_come_from_cited_evidence():
    evidence = build_evidence(OUTPUTS, CANDIDATE)
    ids = {row["label"]: row["id"] for row in evidence}
    good = {"summary": "", "risks": [], "reasoning": [
        {"claim": "季度收入增加 198,286.78，执行后 OTIF 为 0.563", "evidence_ids": [ids["季度收入变化"], ids["执行后 OTIF"]]},
        {"claim": "增量成本为 22,715.55", "evidence_ids": [ids["增量成本（加急费/返工/采购）"]]}]}
    check = verify(good, evidence)
    assert check["checks_passed"] is True
    assert check["verified"] is False
    invented = {"summary": "", "risks": [], "reasoning": [{"claim": "收入增加 250,000", "evidence_ids": [ids["季度收入变化"]]}]}
    result = verify(invented, evidence)
    assert result["verified"] is False and result["issues"][0]["problem"] == "unsupported_number"
    wrong_ref = {"summary": "", "risks": [], "reasoning": [{"claim": "x", "evidence_ids": ["E999"]}]}
    assert verify(wrong_ref, evidence)["issues"][0]["problem"] == "unknown_evidence"
    # A correct number cited from the wrong evidence is still rejected.
    miscited = {"summary": "", "risks": [], "reasoning": [{"claim": "收入增加 198,286.78", "evidence_ids": [ids["基线 OTIF"]]}]}
    assert verify(miscited, evidence)["verified"] is False


def test_matching_number_does_not_verify_an_inverted_business_claim():
    evidence = [{"id": "E1", "value": {"revenue_protected": 550000}}]
    answer = {"summary": "Unfounded summary", "reasoning": [
        {"claim": "Revenue at risk increases to 550000", "evidence_ids": ["E1"]}], "risks": []}
    result = verify(answer, evidence)
    assert result["checks_passed"] is True
    assert result["verified"] is False
    assert result["semantic_status"] == "not_checked"
    assert result["unchecked_sections"] == ["summary"]


def test_small_invented_counts_and_rescaled_numbers_are_not_exempt():
    evidence = [{"id": "E1", "value": 22715.55}]
    for claim in ("Protected 2 orders", "Cost is 2.27", "Cost is 2271555"):
        answer = {"reasoning": [{"claim": claim, "evidence_ids": ["E1"]}], "risks": []}
        assert verify(answer, evidence)["checks_passed"] is False
