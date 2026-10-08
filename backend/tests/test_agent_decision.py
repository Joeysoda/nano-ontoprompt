from app.services.v2.agent_decision import AgentToolbox


def test_deterministic_agent_requires_real_reasoning_basis(db, ontology):
    toolbox = AgentToolbox(ontology["id"], db)
    try:
        toolbox.propose("检查设备")
    except ValueError as exc:
        assert "推理结论" in str(exc) or "推理运行" in str(exc)
    else:
        raise AssertionError("an Agent proposal must be grounded in a reasoning conclusion")
