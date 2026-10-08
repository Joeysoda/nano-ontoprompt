"""Real FalkorDB compatibility tests, isolated from all user ontologies."""
import uuid
import pytest
from app.services.v2.graph.decision_analysis import DecisionGraphAdapter, analyze


@pytest.fixture
def graph():
    oid = 'decision_test_' + uuid.uuid4().hex
    adapter = DecisionGraphAdapter(oid)
    for ident in ('A','B','C','X'):
        adapter.execute_query('CREATE (:Decision {decision_id:$id, scenario:$id, confidence:0.8, timestamp:"2026-01-01T00:00:00Z"})', {'id': ident})
    adapter.execute_query('MATCH (a:Decision {decision_id:"A"}), (b:Decision {decision_id:"B"}), (c:Decision {decision_id:"C"}), (x:Decision {decision_id:"X"}) CREATE (a)-[:CAUSED {weight:0.8, recorded_at:"2026-01-01T00:00:00Z"}]->(b), (b)-[:INFLUENCED {weight:0.5, recorded_at:"2026-01-03T00:00:00Z"}]->(c), (a)-[:ABOUT]->(x)')
    try:
        yield oid, adapter
    finally:
        # Only the random graph created by this fixture is removed.
        adapter.graph.delete()


def test_direction_time_and_distance(graph):
    oid, _ = graph
    forward = analyze(oid, 'chain', 'A')['result']
    assert {r['decision_id'] for r in forward} == {'B','C'}
    reverse = analyze(oid, 'chain', 'C', direction='upstream')['result']
    assert {r['decision_id'] for r in reverse} == {'A','B'}
    early = analyze(oid, 'time', 'A', at_time='2026-01-02T00:00:00Z')['result']
    assert [r['decision_id'] for r in early] == ['B']
    distance = analyze(oid, 'distance', 'A', 'C')['result']
    assert distance['causal_path'] == ['A','B','C']
    assert distance['confidence_decay'] == 0.4
    assert analyze(oid, 'distance', 'C', 'A')['result']['causal_path'] == []
    assert analyze(oid, 'distance', 'A', 'X')['result']['causal_path'] == []


def test_loop_is_really_detected(graph):
    oid, adapter = graph
    assert analyze(oid, 'loops')['result'] == []
    adapter.execute_query('MATCH (a:Decision {decision_id:"A"}), (c:Decision {decision_id:"C"}) CREATE (c)-[:CAUSED]->(a)')
    result = analyze(oid, 'loops')['result']
    assert result
    assert any(r['loop_length'] == 3 for r in result)
    assert {r['decision_id'] for r in analyze(oid,'chain','A')['result']} >= {'B','C'}


def test_precedent_score_and_network(graph):
    oid, adapter = graph
    adapter.execute_query('MATCH (a:Decision {decision_id:"A"}), (c:Decision {decision_id:"C"}) CREATE (a)-[:PRECEDENT_FOR {type:"PRECEDENT_FOR"}]->(c)')
    assert [r['decision_id'] for r in analyze(oid,'precedents','A')['result']] == ['C']
    assert [r['decision_id'] for r in analyze(oid,'roots','C')['result']] == ['A']
    assert 0 < analyze(oid,'score','A')['result'] <= 1
    network = analyze(oid,'network')['result']
    assert network['node_count'] == 4
    assert network['edge_count'] == 2


def test_bad_query_is_not_empty_result(graph):
    _, adapter = graph
    with pytest.raises(Exception):
        adapter.execute_query('THIS IS NOT CYPHER')
    assert adapter.last_error is not None
