import pytest


@pytest.mark.parametrize('suffix,method,body', [
    ('keyword?q=machine', 'get', None),
    ('semantic?q=machine', 'get', None),
    ('objects', 'post', {'type_name': 'Machine', 'query': 'machine'}),
])
def test_search_requires_ontology_access(client, ontology, editor_user, suffix, method, body):
    token = client.post('/api/v1/auth/login', json={'username': 'editor', 'password': 'editor123'}).json()['data']['access_token']
    kwargs = {'headers': {'Authorization': 'Bearer ' + token}}
    if body is not None:
        kwargs['json'] = body
    response = getattr(client, method)(f'/api/v2/ontologies/{ontology["id"]}/search/{suffix}', **kwargs)
    assert response.status_code == 404


def test_semantic_dependency_failure_is_not_empty(client, ontology, auth_headers, monkeypatch):
    from app.services.v2.vector import chroma_service
    class Unavailable:
        available = False
    monkeypatch.setattr(chroma_service, 'ChromaService', Unavailable)
    response = client.get(f'/api/v2/ontologies/{ontology["id"]}/search/semantic?q=machine', headers=auth_headers)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'search_unavailable'
