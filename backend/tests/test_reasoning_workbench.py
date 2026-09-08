"""Uses the installed, pinned Semantica engine, not a mock reasoner."""
import pytest
from app.routers.v2.reasoning_workbench import normalize, infer

def test_whitespace_and_multihop_evidence():
    facts, rules = normalize(['A(a,b)', 'B(b,c)'], ['IF A(?x,?y) AND B(?y,?z) THEN C(?x,?z)', 'IF C(?x,?z) THEN D(?x,?z)'])
    output = {r['conclusion']: r for r in infer(facts, rules, {'A(a, b)': [{'fact': 'A(a, b)', 'evidence_ref_id': 'real-row-1'}], 'B(b, c)': [{'fact': 'B(b, c)', 'evidence_ref_id': 'real-row-2'}]})}
    assert set(output) == {'C(a, c)', 'D(a, c)'}
    assert output['D(a, c)']['premises'] == ['C(a, c)']
    assert {e['evidence_ref_id'] for e in output['D(a, c)']['evidence']} == {'real-row-1', 'real-row-2'}

def test_alternative_proofs_not_merged_into_wrong_rule():
    output = infer(['A(a)', 'B(a)'], ['IF A(?x) THEN C(?x)', 'IF B(?x) THEN C(?x)'], {})
    assert output[0]['premises'] == ['A(a)']
    assert output[0]['rule'] == 'IF A(?x) THEN C(?x)'
    assert output[0]['proof_scope'] == 'one_grounded_derivation'

@pytest.mark.parametrize('rules', [['nonsense'], ['IF A(?x) THEN B(?missing)'], ['IF A(?x) THEN B(?x) AND C(?x)'], ['IF A(?x) THEN A(?x, c)']])
def test_invalid_rules_rejected(rules):
    with pytest.raises(ValueError):
        normalize(['A(a)'], rules)

def test_manual_input_not_given_fake_file_provenance():
    output = infer(['A(a)'], ['IF A(?x) THEN B(?x)'], {})
    assert output[0]['evidence'] == [{'fact': 'A(a)', 'source': 'manual_input'}]
