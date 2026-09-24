"""Result-oriented contract_v2 tests; bounded adapter returns actual graph data."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.schemas.v2.object_query import LoadObjectSetRequest, ParameterSpec
from app.services.v2.object_query.core import QueryCore, QueryPolicy, GraphData
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import OntologyMetadata, TypeMetadata, PropertyMetadata, LinkMetadata
from app.services.v2.object_query.normalize import canonical_data, stable_hash, definition_hash
from app.services.v2.object_query.logic_binding import reject_page_binding


def base(): return {'kind': 'base', 'type_ref': {'api_name': 'T'}}
def static(*ids): return {'kind': 'static', 'type_ref': {'api_name': 'T'}, 'object_ids': list(ids)}
def where(field='n', op='gt', value=1): return {'kind': 'comparison', 'property': {'api_name': field}, 'op': op, 'value': value}
def filtered(expr=None, condition=None): return {'kind': 'filter', 'input': expr or base(), 'where': condition or where()}
def request(expr, **kwargs):
    payload = {'expression': expr, 'context': {'ontology_id': 'o'}}
    payload.update(kwargs)
    return LoadObjectSetRequest.model_validate(payload)


@pytest.fixture
def metadata():
    return OntologyMetadata([TypeMetadata('T', properties={
        'n': PropertyMetadata('n', 'integer'), 's': PropertyMetadata('s'),
        'd': PropertyMetadata('d', 'date'), 'money': PropertyMetadata('money', 'decimal'),
        'tags': PropertyMetadata('tags', 'array', is_array=True),
    })], [LinkMetadata('next', 'T', 'T')])


class Adapter:
    def __init__(self, data): self.data = data
    def read(self, ontology_id): return deepcopy(self.data)


@pytest.fixture
def data():
    return GraphData({('T', 'a'): {'n': 1, 's': 'A', 'd': '2026-01-01', 'money': {'$decimal': '0.10'}, 'tags': ['x', 'y']},
        ('T', 'b'): {'n': 2, 's': 'B', 'd': '2026-02-01', 'money': {'$decimal': '0.20'}, 'tags': ['y']},
        ('T', 'c'): {'n': None, 's': None}}, [(('T', 'a'), 'NEXT', ('T', 'b'))])


def ids(result): return [o.object_id for o in result.objects]
def core(metadata, data, **policy): return QueryCore(metadata, Adapter(data), QueryPolicy('u', **policy))


@pytest.mark.parametrize('expr,expected', [
    (base(), ['a','b','c']), (static(), []), ({'kind': 'empty', 'type_ref': {'api_name':'T'}}, []),
    (filtered(), ['b']), ({'kind':'traverse','input':static('a'),'link':{'api_name':'next'}}, ['b']),
    ({'kind':'union','inputs':[static('a','b'),static('b','c')]}, ['a','b','c']),
    ({'kind':'intersect','inputs':[static('a','b'),static('b','c')]}, ['b']),
    ({'kind':'subtract','base':static('a','b'),'subtract':[static('b','c')]}, ['a']),
])
def test_membership(metadata, data, expr, expected):
    result = core(metadata,data).load(request(expr))
    assert ids(result) == expected
    assert result.completeness == 'complete'
    assert result.status == ('ok' if expected else 'empty')
    assert len(result.definition_hash) == len(result.execution_hash) == 64


@pytest.mark.parametrize('quantifier,expected', [('any',['a']),('none',['b','c']),('all',['a','b','c']),('count',['a'])])
def test_linked_quantifiers(metadata, data, quantifier, expected):
    expr = {'kind':'linked','input':base(),'link':{'api_name':'next'},'where':where(), 'quantifier':quantifier}
    assert ids(core(metadata,data).load(request(expr))) == expected
    # Hidden targets cannot leak through any/none/all.
    result = core(metadata,data,object_ids=frozenset({('T','a')})).load(request(expr))
    assert ids(result) == ([] if quantifier in ('any','count') else ['a'])


@pytest.mark.parametrize('condition,expected', [
    (where('n','in',[]),[]), ({'kind':'not','item':where('n','in',[])},['a','b','c']),
    (where('n','eq',None),[]), ({'kind':'null_test','property':{'api_name':'n'}},['c']),
    (where('d','gte','2026-02-01'),['b']), (where('money','eq',{'$decimal':'0.100'}),['a']),
    ({'kind':'array_match','property':{'api_name':'tags'},'mode':'contains_all','values':['x','y']},['a']),
])
def test_value_semantics(metadata,data,condition,expected):
    assert ids(core(metadata,data).load(request(filtered(condition=condition)))) == expected


def test_hash_copy_and_null():
    expr = request(filtered(condition=where('n','in',[1,2]))).expression
    canonical = canonical_data(expr)
    before = stable_hash(canonical)
    expr.where.value.append(3)
    assert stable_hash(canonical) == before
    assert canonical['where']['value'] == [1,2]
    assert stable_hash({'x':None}) != stable_hash({})
    assert stable_hash(Decimal('1.00')) == stable_hash(Decimal('1'))
    assert definition_hash(request(static()).expression) == definition_hash(request({'kind':'empty','type_ref':{'api_name':'T'}}).expression)
    assert definition_hash(request(static('a','b','a')).expression) == definition_hash(request(static('b','a')).expression)


def test_parameters_types_ranges_fields_and_injection(metadata,data):
    ref = {'kind':'parameter','name':'threshold','data_type':'integer'}
    schema = {'threshold': {'data_type':'integer','default':1,'minimum':0,'maximum':10,'allowed_fields':['n']}}
    expr = filtered(condition=where(value=ref))
    assert ids(core(metadata,data).load(request(expr, parameter_schema=schema))) == ['b']
    for value in ['1', "1) MATCH (secret)", -1, 11, True]:
        with pytest.raises(ObjectQueryError, match='Parameter|Value'):
            core(metadata,data).load(request(expr,parameter_schema=schema,parameters={'threshold':value}))
    schema['threshold']['allowed_fields'] = ['s']
    with pytest.raises(ObjectQueryError) as error:
        core(metadata,data).load(request(expr,parameter_schema=schema))
    assert error.value.code == 'invalid_parameter'
    with pytest.raises(ValidationError):
        request({**base(), 'cypher':'MATCH (n) RETURN n'})


def test_page_is_not_logic_input(metadata,data):
    result=core(metadata,data).load(request(base(),read={'page_size':1}))
    assert result.page.has_more and result.completeness == result.status == 'partial'
    with pytest.raises(ObjectQueryError) as error:
        reject_page_binding({}, {'input':result.model_dump()})
    assert error.value.code == 'complete_set_unavailable'
    with pytest.raises(ObjectQueryError):
        reject_page_binding({'binding_spec':{'requires_complete_set':True}}, {'objects':['a']})


def test_complete_snapshot_set_handle_is_accepted():
    reject_page_binding({'binding_spec': {'kind': 'set_handle'}}, {
        'set': {'kind': 'set_handle', 'consistency': 'snapshot', 'completeness': 'complete'}
    })


def test_snapshot_materialize_returns_complete_set(metadata, data):
    request_data = request(base(), context={'ontology_id': 'o', 'data_view_id': 'view-1', 'consistency': 'snapshot'}, read={'page_size': 1})
    objects, last = core(metadata, data).materialize(request_data)
    assert ids(last) == ['a', 'b', 'c']
    assert [item.object_id for item in objects] == ['a', 'b', 'c']
    assert last.completeness == 'complete'


def test_snapshot_keyset_cursor_is_opaque_and_bound_to_execution(metadata, data):
    first_request = request(base(), read={'page_size': 1}, context={'ontology_id': 'o', 'data_view_id': 'view-1', 'consistency': 'snapshot'})
    first = core(metadata, data).load(first_request)
    assert first.page.stability == 'snapshot_keyset'
    assert first.page.has_more and first.page.next_page_token
    second_request = request(base(), read={'page_size': 1, 'page_token': first.page.next_page_token}, context={'ontology_id': 'o', 'data_view_id': 'view-1', 'consistency': 'snapshot'})
    second = core(metadata, data).load(second_request)
    assert ids(second) == ['b']
    with pytest.raises(ObjectQueryError) as error:
        core(metadata, data).load(request(base(), read={'page_size': 2, 'page_token': first.page.next_page_token}, context={'ontology_id': 'o', 'data_view_id': 'view-1', 'consistency': 'snapshot'}))
    assert error.value.code == 'cursor_mismatch'
    changed = request(base(), read={'page_size': 1, 'page_token': first.page.next_page_token}, context={'ontology_id': 'o', 'data_view_id': 'view-2', 'consistency': 'snapshot'})
    with pytest.raises(ObjectQueryError) as error:
        core(metadata, data).load(changed)
    assert error.value.code == 'cursor_mismatch'


def test_permissions_and_metadata(metadata,data):
    restricted=core(metadata,data,fields={'T':frozenset({'s'})})
    assert set(restricted.load(request(base())).objects[0].properties) == {'s'}
    for req in [request(filtered()),request(base(),read={'order_by':[{'property':{'api_name':'n'}}]}),request(base(),read={'select':[{'api_name':'n'}]})]:
        with pytest.raises(ObjectQueryError) as error: restricted.load(req)
        assert error.value.code == 'forbidden'
    req=request(base()).model_copy(update={'context':request(base()).context.model_copy(update={'metadata_digest':'old'})})
    with pytest.raises(ObjectQueryError) as error: core(metadata,data).load(req)
    assert error.value.code == 'metadata_mismatch'


def test_reachability_cycle_and_depth(metadata,data):
    data.edges += [(('T','b'),'NEXT',('T','c')), (('T','c'),'NEXT',('T','a'))]
    expr={'kind':'reachable','input':static('a'),'link':{'api_name':'next'},'max_depth':1}
    result=core(metadata,data).load(request(expr))
    assert ids(result)==['b'] and result.completeness=='incomplete'
    expr['max_depth']=8
    result=core(metadata,data).load(request(expr))
    assert ids(result)==['b','c'] and result.completeness=='complete'
    expr['max_nodes']=1
    assert core(metadata,data).load(request(expr)).status=='incomplete'


def test_invalid_date_decimal_array_and_unknown_type(metadata,data):
    for condition in [where('d','eq','not-a-date'),where('money','eq',0.1),where('tags','eq','x')]:
        with pytest.raises(ObjectQueryError): core(metadata,data).load(request(filtered(condition=condition)))
    with pytest.raises(ObjectQueryError) as error:
        core(metadata,data).load(request({'kind':'base','type_ref':{'api_name':'missing'}}))
    assert error.value.code=='unknown_type'
