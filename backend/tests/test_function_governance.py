from types import SimpleNamespace
import pytest
from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.v2.logic_asset import LogicAsset
from app.services.v2.function_contracts import matches_version, resolve_version, validate_domain
from app.services.v2.function_binding import function_resolver
from app.services.v2.logic_assets import EXECUTORS, execute
from app.services.v2.logic_contracts import ContractValidationError
from app.services.v2.object_query.metadata import OntologyMetadata, TypeMetadata
from app.services.v2.scenario_actions import compile_actions
from app.services.v2.scenarios import ScenarioError


def test_semver_resolves_numerically_and_pins_stable_release():
    versions = [SimpleNamespace(version=value, status='published') for value in ('1.2.9', '1.10.0', '2.0.0')]
    assert resolve_version(versions, '^1.2.0').version == '1.10.0'
    assert resolve_version(versions, '~1.2.0').version == '1.2.9'
    assert not matches_version('0.2.0', '^0.1.0')
    assert not matches_version('0.0.2', '^0.0.1')
    with pytest.raises(ContractValidationError):
        resolve_version(versions, 'latest')


def test_domain_object_cannot_reference_missing_or_wrong_type():
    schema = {'x-ontology': {'kind': 'object', 'api_name': 'Ticket'}}
    metadata = OntologyMetadata([TypeMetadata('Ticket')], [])
    snapshot = SimpleNamespace(objects={('Ticket', 'one'): {}})
    validate_domain(schema, {'object_type': 'Ticket', 'object_id': 'one'}, metadata=metadata, snapshot=snapshot, ontology_id='ont')
    with pytest.raises(ContractValidationError):
        validate_domain(schema, {'object_type': 'Ticket', 'object_id': 'hidden'}, metadata=metadata, snapshot=snapshot, ontology_id='ont')
    with pytest.raises(ContractValidationError):
        validate_domain(schema, {'object_type': 'Ticket', 'object_id': 'one'})


def test_edit_function_only_runs_inside_shared_action_compiler(db, admin_user, monkeypatch):
    db.add(OntologyProject(id='function-ont', name='Functions', domain='test', created_by=admin_user.id))
    db.add(Entity(id='type', ontology_id='function-ont', name_cn='Ticket', name_en='Ticket', type='EntityType', properties={'property_definitions': [{'id': 'status', 'type': 'string'}]}))
    emitted = {'edits': [{'sequence': 0, 'op': 'set_property', 'target': {'ontology_id': 'function-ont', 'concrete_type': 'Ticket', 'object_id': 'one'}, 'property': 'status', 'value': 'closed', 'expected_old_value': 'open'}]}
    calls = []
    monkeypatch.setitem(EXECUTORS, 'test_edit', lambda inputs: calls.append(inputs) or emitted)
    asset = LogicAsset(ontology_id='function-ont', asset_key='close', name='Close', kind='edit', implementation='test_edit', version='1.0.0', input_schema={'type': 'object'}, output_schema={'type': 'object'}, config={'execution_class': 'edit', 'edit_provenance': {'Ticket': ['set_property']}}, status='published', side_effect=False)
    db.add(asset); db.commit()
    contract = {field: getattr(asset, field) for field in ('asset_key', 'implementation', 'input_schema', 'output_schema', 'config')}
    with pytest.raises(ContractValidationError):
        execute(contract, {})
    assert calls == []
    action = SimpleNamespace(id='close-action', version=1, enabled=True, status='published', effects=[], parameters=[], submission_criteria=[], permission_rules=[], backed_by_function='close@1.0.0')
    snapshot = SimpleNamespace(objects={('Ticket', 'one'): {'status': 'open'}}, edges=[])
    invocation = [{'action_type_id': action.id, 'parameters': {}}]
    edits = compile_actions('function-ont', 'live', invocation, {action.id: action}, snapshot, actor=admin_user, function_resolver=function_resolver(db, 'function-ont'))
    assert edits[1].op == 'set_property'
    assert edits[1].value == 'closed'
    assert edits[1].expected_old_value == 'open'
    assert edits[0].parameters['function']['version'] == '1.0.0'
    assert snapshot.objects[('Ticket', 'one')]['status'] == 'open'
    emitted['edits'][0]['target']['concrete_type'] = 'HiddenType'
    with pytest.raises(ScenarioError) as caught:
        compile_actions('function-ont', 'live', invocation, {action.id: action}, snapshot, actor=admin_user, function_resolver=function_resolver(db, 'function-ont'))
    assert caught.value.code == 'function_contract_failed'


def test_permission_rule_combines_principal_and_role():
    action = SimpleNamespace(id='a', enabled=True, status='published', permission_rules=[{'role': 'editor', 'principal_id': 'allowed'}], effects=[])
    with pytest.raises(ScenarioError) as caught:
        compile_actions('ont', 'live', [{'action_type_id': 'a', 'parameters': {}}], {'a': action}, SimpleNamespace(objects={}, edges=[]), actor=SimpleNamespace(id='other', role='editor'))
    assert caught.value.code == 'forbidden'
