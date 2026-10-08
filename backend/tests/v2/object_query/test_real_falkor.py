"""Real fixed FalkorDB integration, on a disposable graph only."""
import os
from uuid import uuid4
import pytest
from app.schemas.v2.object_query import LoadObjectSetRequest
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import QueryCore, QueryPolicy, FalkorReadAdapter
from app.services.v2.object_query.metadata import OntologyMetadata, TypeMetadata, PropertyMetadata, LinkMetadata
from app.services.v2.object_query.errors import ObjectQueryError


@pytest.fixture
def real_graph():
    svc=FalkorDBService()
    if not svc.available:
        pytest.fail('Real FalkorDB is required for Object Set acceptance')
    oid='object-set-test-'+uuid4().hex
    svc.upsert_instances(oid,[{'id':i,'entity_type':'T','properties':p} for i,p in [
        ('a',{'n':1,'tags':['x','y']}),('b',{'n':2,'tags':['y']}),('c',{'n':3})]])
    svc.upsert_relations(oid,[{'source':'a','target':'b','type':'next'},{'source':'b','target':'c','type':'next'}])
    try: yield oid,svc._graph(oid)
    finally: assert svc.delete_graph(oid)


def test_real_falkor_algebra_links_parameters_and_budget(real_graph):
    oid,graph=real_graph
    metadata=OntologyMetadata([TypeMetadata('T',properties={'n':PropertyMetadata('n','integer'),'tags':PropertyMetadata('tags','array',is_array=True)})],[LinkMetadata('next','T','T')])
    core=QueryCore(metadata,FalkorReadAdapter(graph),QueryPolicy('u'))
    base={'kind':'base','type_ref':{'api_name':'T'}}
    def static(*ids): return {'kind':'static','type_ref':{'api_name':'T'},'object_ids':list(ids)}
    def load(expr,**kwargs): return core.load(LoadObjectSetRequest(expression=expr,context={'ontology_id':oid},**kwargs))
    def ids(result): return [v.object_id for v in result.objects]
    cases=[({'kind':'union','inputs':[static('a','b'),static('b','c')]},['a','b','c']),
           ({'kind':'intersect','inputs':[static('a','b'),static('b','c')]},['b']),
           ({'kind':'subtract','base':base,'subtract':[static('b')]},['a','c']),
           ({'kind':'traverse','input':static('a'),'link':{'api_name':'next'}},['b'])]
    for expr,expected in cases: assert ids(load(expr))==expected
    condition={'kind':'comparison','property':{'api_name':'n'},'op':'gt','value':2}
    for quantifier,expected in [('any',['b']),('none',['a','c']),('all',['b','c'])]:
        assert ids(load({'kind':'linked','input':base,'link':{'api_name':'next'},'where':condition,'quantifier':quantifier}))==expected
    condition['value']={'kind':'parameter','name':'threshold','data_type':'integer'}
    result=load({'kind':'filter','input':base,'where':condition},parameter_schema={'threshold':{'data_type':'integer','default':1,'allowed_fields':['n']}},read={'page_size':1,'order_by':[{'property':{'api_name':'n'},'direction':'desc'}]})
    assert ids(result)==['c'] and result.completeness=='partial'
    assert load(static()).status=='empty'
    with pytest.raises(ObjectQueryError) as error: FalkorReadAdapter(graph,max_objects=1).read(oid)
    assert error.value.code=='query_too_complex'


def test_real_falkor_saved_resource(db,admin_user,real_graph):
    from app.models.ontology import OntologyProject
    from app.schemas.v2.object_set import CreateObjectSet
    from app.services.v2.object_query.resources import ResourceService
    oid,graph=real_graph
    db.add(OntologyProject(id=oid,name='isolated graph',domain='test',created_by=admin_user.id));db.commit()
    metadata=OntologyMetadata([TypeMetadata('T',properties={'n':PropertyMetadata('n','integer')})],[])
    svc=ResourceService(db,oid,admin_user,metadata)
    row=svc.create(CreateObjectSet(name='saved',definition={'expression':{'kind':'static','type_ref':{'api_name':'T'},'object_ids':['b','a']}}))
    result=QueryCore(metadata,FalkorReadAdapter(graph),QueryPolicy(admin_user.id)).load(LoadObjectSetRequest(
        expression={'kind':'reference','object_set_id':row.id,'definition_version':1},context={'ontology_id':oid}),svc)
    assert [v.object_id for v in result.objects]==['a','b']
    assert result.execution_context['references'][0]['definition_hash']==svc.version(row.id).definition_hash


def test_graph_unavailable_is_not_empty():
    class Broken:
        def ro_query(self,*args,**kwargs): raise ConnectionError('offline')
    with pytest.raises(ObjectQueryError) as error: FalkorReadAdapter(Broken()).read('o')
    assert error.value.code=='graph_unavailable'


def test_real_data_view_is_immutable_manifest_and_exact_aggregate(db, admin_user, real_graph):
    from app.models.ontology import OntologyProject
    from app.schemas.v2.object_query import AggregateObjectSetRequest
    from app.services.v2.object_query.data_views import build_live_view, require_ready
    from app.services.v2.object_query.metadata import OntologyMetadata
    from app.services.v2.object_query.normalize import stable_hash

    oid, graph = real_graph
    db.add(OntologyProject(id=oid, name='view graph', domain='test', created_by=admin_user.id))
    db.commit()
    service = FalkorDBService()
    metadata = OntologyMetadata([TypeMetadata('T', properties={
        'n': PropertyMetadata('n', 'integer'),
        'tags': PropertyMetadata('tags', 'array', is_array=True),
    })], [LinkMetadata('next', 'T', 'T')])
    view = build_live_view(db, service, metadata, oid, admin_user.id, stable_hash({'source': oid}), 3600)
    assert view.status == 'ready'
    assert view.object_count == 3 and view.edge_count == 2
    assert require_ready(db, view.id, oid).content_digest == view.content_digest
    view_graph = service._graph(view.graph_key)
    core = QueryCore(metadata, FalkorReadAdapter(view_graph, graph_ontology_id=view.graph_key), QueryPolicy(admin_user.id))
    result = core.aggregate(AggregateObjectSetRequest(
        expression={'kind': 'base', 'type_ref': {'api_name': 'T'}},
        aggregations=[{'op': 'count'}, {'op': 'sum', 'property': {'api_name': 'n'}, 'alias': 'total'}],
        context={'ontology_id': oid, 'data_view_id': view.id, 'consistency': 'snapshot'},
    ))
    assert result.exact is True and result.groups == [{'value': 3, 'total': 6}]
