import pytest
from fastapi import HTTPException
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.routers.v2.object_query import query_catalog
from app.services.v2.object_query.metadata import load_sql_metadata


def test_catalog_uses_query_identifiers_and_excludes_instances(db, admin_user, editor_user):
    db.add(OntologyProject(id='catalog', name='Catalog', domain='test', created_by=admin_user.id))
    db.add(Entity(id='type', ontology_id='catalog', name_cn='订单', name_en='LegacyOrder', type='EntityType',
                  canonical_id='ontology:Order', properties={'property_definitions': [{'id': 'amount', 'type': 'int'}]}))
    db.add(Entity(id='instance', ontology_id='catalog', name_cn='one', type='Order', properties={'is_instance': True}))
    db.commit()
    result = query_catalog('catalog', db, admin_user)
    assert [item['api_name'] for item in result['types']] == ['Order']
    assert result['types'][0]['properties'] == load_sql_metadata(db, 'catalog').catalog()['types'][0]['properties']
    assert result['metadata_digest']
    with pytest.raises(HTTPException) as caught:
        query_catalog('catalog', db, editor_user)
    assert caught.value.status_code == 404
