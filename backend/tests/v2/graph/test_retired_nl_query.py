from unittest.mock import patch


def test_legacy_natural_language_query_never_opens_a_graph(client, auth_headers, ontology):
    with patch("app.routers.v2.graph.get_neo4j") as graph:
        response = client.post(
            f"/api/v2/ontologies/{ontology['id']}/graph/ask",
            json={"question": "show every object"}, headers=auth_headers,
        )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_nl_query_retired"
    graph.assert_not_called()
