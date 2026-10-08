from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.v2 import object_views
from app.models.entity import Entity
from app.models.ontology import OntologyProject


def test_publish_rejects_unpublished_property_before_writing(monkeypatch):
    monkeypatch.setattr(object_views, "_metadata", lambda *args: SimpleNamespace(properties={"status": object()}))
    config = object_views.ViewConfig(tabs=[object_views.ViewSection(id="summary", title="Summary", properties=["secret"])])
    with pytest.raises(HTTPException) as caught:
        object_views.publish_object_view("ont", "Order", config, db=object(), user=object())
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "unknown_property"


def test_publish_rejects_duplicate_tab_ids(monkeypatch):
    monkeypatch.setattr(object_views, "_metadata", lambda *args: SimpleNamespace(properties={"status": object()}))
    tab = object_views.ViewSection(id="summary", title="Summary", properties=["status"])
    with pytest.raises(HTTPException) as caught:
        object_views.publish_object_view("ont", "Order", object_views.ViewConfig(tabs=[tab, tab]), db=object(), user=object())
    assert caught.value.detail["code"] == "duplicate_tab"


def test_published_view_falls_back_to_standard_after_metadata_change(db, admin_user):
    db.add(OntologyProject(id="view-ont", name="Views", domain="test", created_by=admin_user.id))
    entity = Entity(id="view-type", ontology_id="view-ont", name_cn="Order", name_en="Order", type="EntityType",
                    properties={"property_definitions": [{"id": "status", "type": "string"}]})
    db.add(entity)
    db.commit()
    config = object_views.ViewConfig(tabs=[object_views.ViewSection(id="summary", title="Summary", properties=["status"])])
    published = object_views.publish_object_view("view-ont", "Order", config, db, admin_user)
    assert published["version"] == 1
    current = object_views.get_object_view("view-ont", "Order", db, admin_user)
    assert current["configured"]["tabs"][0]["id"] == "summary"
    assert current["configuration_stale"] is False
    entity.properties = {"property_definitions": [{"id": "status", "type": "string"}, {"id": "priority", "type": "integer"}]}
    db.commit()
    stale = object_views.get_object_view("view-ont", "Order", db, admin_user)
    assert stale["configuration_stale"] is True
    assert stale["configured"] is None
    assert {"status", "priority"} <= set(stale["standard"]["tabs"][0]["properties"])
