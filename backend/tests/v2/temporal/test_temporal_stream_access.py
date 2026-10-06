"""Temporal stream URLs must respect ontology ownership before returning data."""
from uuid import uuid4

from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.temporal_replay import TemporalReplay
from app.services.auth_service import hash_password


def test_stream_routes_hide_another_users_ontology(client, db):
    owner = User(id=str(uuid4()), username="stream_owner", email="stream_owner@test.com",
                 password_hash=hash_password("password123"), role="editor")
    outsider = User(id=str(uuid4()), username="stream_outsider", email="stream_outsider@test.com",
                    password_hash=hash_password("password123"), role="editor")
    db.add_all([owner, outsider])
    db.flush()
    ontology = OntologyProject(id=str(uuid4()), name="private stream", domain="test", created_by=owner.id)
    db.add(ontology)
    db.flush()
    replay = TemporalReplay(id=str(uuid4()), ontology_id=ontology.id, source_id="factorynet_cnc")
    db.add(replay)
    db.commit()

    login = client.post("/api/v1/auth/login", json={"username": outsider.username, "password": "password123"})
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    paths = [
        ("get", f"/api/v2/ontologies/{ontology.id}/temporal-streams", None),
        ("post", f"/api/v2/ontologies/{ontology.id}/temporal-streams", {}),
        ("get", f"/api/v2/temporal-streams/{replay.id}", None),
        ("get", f"/api/v2/temporal-streams/{replay.id}/events", None),
        ("get", f"/api/v2/temporal-streams/{replay.id}/facts", None),
        ("get", f"/api/v2/temporal-streams/{replay.id}/graph", None),
        ("get", f"/api/v2/temporal-streams/{replay.id}/event-stream", None),
        ("post", f"/api/v2/temporal-streams/{replay.id}/control", {"action": "pause"}),
        ("post", f"/api/v2/temporal-streams/{replay.id}/events", {
            "event_id": "e1", "episode_id": "ep1", "entity_key": "x",
            "ordinal": 1, "source_sequence": 1,
        }),
        ("post", f"/api/v2/temporal-streams/{replay.id}/publish", None),
    ]
    for method, path, body in paths:
        response = client.request(method, path, headers=headers, json=body)
        assert response.status_code == 404, (method, path, response.text)
