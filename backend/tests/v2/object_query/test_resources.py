from datetime import timedelta
from uuid import uuid4
import pytest
from app.models.ontology import OntologyProject
from app.models.v2.object_set import ObjectSetResource, ObjectSetVersion, ObjectSetGrant, ObjectSetAudit, ObjectSetStaticMember, ObjectSetDependency, now
from app.schemas.v2.object_set import CreateObjectSet, NewObjectSetVersion, ObjectSetDefinition, GrantObjectSet
from app.schemas.v2.object_query import ReferenceObjectSet
from app.services.v2.object_query.resources import ResourceService
from app.services.v2.object_query.metadata import OntologyMetadata, TypeMetadata
from app.services.v2.object_query.errors import ObjectQueryError


def definition(expr=None): return ObjectSetDefinition(expression=expr or {'kind':'base','type_ref':{'api_name':'T'}})
def ref(rid, version=1): return {'kind':'reference','object_set_id':rid,'definition_version':version}
def create(svc, expr=None, **kwargs): return svc.create(CreateObjectSet(name='set',definition=definition(expr),**kwargs))


@pytest.fixture
def svc(db, admin_user):
    oid=str(uuid4())
    db.add(OntologyProject(id=oid,name='sets',domain='test',created_by=admin_user.id))
    db.commit()
    return ResourceService(db,oid,admin_user,OntologyMetadata([TypeMetadata('T')],[]))


def test_versions_pinned_closure_and_conflict(svc):
    a=create(svc)
    b=create(svc,ref(a.id))
    a2=svc.new_version(a.id,NewObjectSetVersion(expected_version=1,definition=definition({'kind':'empty','type_ref':{'api_name':'T'}})))
    expression, manifest=svc.expand(ReferenceObjectSet(**ref(b.id)))
    assert expression.kind=='base'
    assert {(v['resource_id'],v['version']) for v in manifest}=={(a.id,1),(b.id,1)}
    assert svc.db.query(ObjectSetDependency).filter_by(resource_id=b.id).one().target_version==1
    with pytest.raises(ObjectQueryError) as error:
        svc.new_version(a.id,NewObjectSetVersion(expected_version=1,definition=definition()))
    assert error.value.code=='version_conflict'
    assert svc.version(a.id,1).definition['expression']['kind']=='base'
    assert svc.version(a.id,2).definition['expression']['kind']=='empty'
    row=svc.version(a.id,1)
    row.definition_hash='tampered'
    with pytest.raises(ValueError,match='immutable'): svc.db.flush()
    svc.db.rollback()


def test_cycle_and_missing_version(svc):
    a=create(svc)
    b=create(svc,ref(a.id))
    with pytest.raises(ObjectQueryError) as error:
        svc.new_version(a.id,NewObjectSetVersion(expected_version=1,definition=definition(ref(b.id))))
    assert error.value.code=='reference_cycle'
    with pytest.raises(ObjectQueryError) as error:
        create(svc,{'kind':'reference','object_set_id':a.id})
    assert error.value.code=='invalid_definition'
    svc.db.rollback()


def test_depth_limit(svc):
    item=create(svc)
    for _ in range(16): item=create(svc,ref(item.id))
    with pytest.raises(ObjectQueryError) as error:
        svc.expand(ReferenceObjectSet(**ref(item.id)))
    assert error.value.code=='query_too_complex'


def test_static_audit_and_expiry(svc):
    row=create(svc,{'kind':'static','type_ref':{'api_name':'T'},'object_ids':['a','a','b']},lifecycle='temporary')
    assert svc.db.query(ObjectSetStaticMember).filter_by(resource_id=row.id).count()==2
    audit=svc.db.query(ObjectSetAudit).filter_by(resource_id=row.id).one()
    assert set(audit.summary)=={'etag'} and len(audit.definition_hash)==64
    row.expires_at=now()-timedelta(seconds=1)
    svc.db.commit()
    with pytest.raises(ObjectQueryError) as error: svc.version(row.id)
    assert error.value.code=='expired_reference'


def test_tombstone_and_lease(svc):
    row=create(svc)
    lease=svc.lease(row.id,1)
    svc.mutate(row.id,row.etag,{'status':'tombstone'},'tombstone')
    svc.db.commit()
    with pytest.raises(ObjectQueryError) as error: svc.expand(ReferenceObjectSet(**ref(row.id)))
    assert error.value.code=='not_found'
    manifest=svc.read_lease(lease.id)
    assert manifest['expression']['kind']=='base'
    lease.expires_at=now()-timedelta(seconds=1)
    svc.db.commit()
    with pytest.raises(ObjectQueryError) as error: svc.read_lease(lease.id)
    assert error.value.code=='expired_reference'


def test_resource_grant_does_not_grant_ontology(svc,editor_user):
    row=create(svc)
    svc.grant(row.id,GrantObjectSet(expected_etag=row.etag,principal_id=editor_user.id,role='view'))
    other=ResourceService(svc.db,svc.ontology_id,editor_user,svc.metadata)
    with pytest.raises(ObjectQueryError) as error: other.get(row.id)
    assert error.value.code=='not_found'
    ontology=svc.db.get(OntologyProject,svc.ontology_id)
    ontology.created_by=editor_user.id
    svc.db.commit()
    assert other.get(row.id).id==row.id
    lease=other.lease(row.id,1)
    with pytest.raises(ObjectQueryError) as error: other.get(row.id,edit=True)
    assert error.value.code=='forbidden'
    svc.db.delete(svc.db.get(ObjectSetGrant,(row.id,editor_user.id)))
    svc.db.commit()
    with pytest.raises(ObjectQueryError) as error: other.read_lease(lease.id)
    assert error.value.code=='not_found'


def test_archive_and_etag(svc):
    row=create(svc)
    svc.mutate(row.id,1,{'status':'archived'},'archive')
    svc.db.commit()
    with pytest.raises(ObjectQueryError) as error: svc.expand(ReferenceObjectSet(**ref(row.id)))
    assert error.value.code=='stale_reference'
    with pytest.raises(ObjectQueryError) as error: svc.mutate(row.id,1,{'name':'lost update'},'update')
    assert error.value.code=='version_conflict'


def test_http_conflict_errors_and_logic_refusal(client,ontology,auth_headers,db):
    from app.models.entity import Entity
    db.add(Entity(id=str(uuid4()),ontology_id=ontology['id'],name_en='T',name_cn='T',type='EntityType'))
    db.commit()
    root=f"/api/v2/ontologies/{ontology['id']}/object-sets/resources"
    response=client.post(root,headers=auth_headers,json={'name':'scope','definition':definition().model_dump()})
    assert response.status_code==201, response.text
    row=response.json()
    assert client.post(root+'/'+row['id']+'/versions',headers=auth_headers,json={'expected_version':9,'definition':definition().model_dump()}).status_code==409
    assert client.post(root+'/'+row['id']+'/set-handle',headers=auth_headers).json()['detail']['code']=='complete_set_unavailable'
    assert client.get(root+'/missing',headers=auth_headers).status_code==404
    assert client.delete(root+'/'+row['id']+'?expected_etag=1',headers=auth_headers).status_code==200
    assert client.get(root+'/'+row['id'],headers=auth_headers).status_code==404
