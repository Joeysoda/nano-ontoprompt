"""Adapt Function-produced edits into the existing Action compiler rules."""
from app.models.v2.logic_asset import LogicAsset
from app.services.v2.function_contracts import resolve_version, execution_class, validate_edits
from app.services.v2.logic_assets import execute
from app.services.v2.logic_contracts import ContractValidationError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.scenarios import ScenarioError


def function_resolver(db, ontology_id):
    def resolve(action, invocation, snapshot):
        binding = str(action.backed_by_function)
        if '@' not in binding:
            raise ScenarioError('function_pin_required', 'Function-backed Actions require asset_key@major.minor.patch')
        key, requirement = binding.rsplit('@', 1)
        # Published Actions always pin a release; dependency ranges are resolved
        # by the discovery endpoint before publication, never during submit.
        if requirement.startswith(('^', '~')):
            raise ScenarioError('function_pin_required', 'Action Function bindings must pin an exact release')
        try:
            asset = resolve_version(db.query(LogicAsset).filter_by(ontology_id=ontology_id, asset_key=key).all(), requirement)
            contract = {field: getattr(asset, field) for field in ('asset_key', 'implementation', 'interface_key', 'version', 'input_schema', 'output_schema', 'config', 'side_effect')}
            if execution_class(contract) != 'edit':
                raise ScenarioError('invalid_function_binding', 'Function-backed Action requires an edit-producing Function')
            result = execute(contract, invocation['parameters'], metadata=load_sql_metadata(db, ontology_id), snapshot=snapshot, ontology_id=ontology_id, through_action=True)
            edits = validate_edits(contract, result['output'], ontology_id)
            if len(edits) > 30:
                raise ScenarioError('invalid_function_edits', 'Action supports at most 30 Function edits')
            definitions, values, rules, ids = [], {}, [], {}
            for index, edit in enumerate(edits):
                rule = {'op': edit.op}
                def ref(name, target):
                    parameter = f'{name}_{index}'
                    definitions.append({'name': parameter, 'type': 'object', 'object_type': target.concrete_type})
                    values[parameter] = {'object_type': target.concrete_type, 'object_id': target.object_id}
                    return parameter
                if edit.op in {'set_property', 'unset_property', 'delete_object'}:
                    rule['target_parameter'] = ref('target', edit.target)
                    if edit.property:
                        rule['property'] = edit.property
                    if edit.op == 'set_property':
                        rule['static_value'] = edit.value
                    if 'expected_old_value' in edit.model_fields_set:
                        props = snapshot.objects.get((edit.target.concrete_type, edit.target.object_id), {})
                        if props.get(edit.property) != edit.expected_old_value:
                            raise ScenarioError('edit_conflict', 'Function expected value differs from execution context', 409)
                elif edit.op == 'create_object':
                    if edit.object_type != edit.target.concrete_type:
                        raise ScenarioError('invalid_function_edits', 'Created object type must match its target')
                    rule.update(object_type=edit.object_type, properties={key: {'static': value} for key, value in edit.properties.items()})
                    ids[index] = edit.target.object_id
                elif edit.op in {'add_link', 'remove_link'}:
                    rule.update(source_parameter=ref('source', edit.link.source), target_parameter=ref('target', edit.link.target), relation_type=edit.link.relation_type)
                rules.append(rule)
            return definitions, values, rules, ids, {'asset_key': key, 'version': asset.version, 'asset_id': asset.id}
        except ContractValidationError as exc:
            raise ScenarioError('function_contract_failed', str(exc), 422, exc.stage) from exc
    return resolve
