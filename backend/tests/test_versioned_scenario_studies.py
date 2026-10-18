"""Contract tests for canonical study authoring and deterministic compilation."""
import copy

import pytest

from app.models.v2.logic_asset import LogicAsset
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioResource
from app.schemas.v2.scenario_study import StudySpec
from app.services.v2.scenario_studies import StudyError, _validate_value, stable_hash, validate_spec


def spec_payload():
    return {
        "spec_version": "scenario-study/v1",
        "parameters": [
            {"key": "demand_multiplier", "type": "decimal", "unit": "ratio", "default": 1.0, "minimum": 0.5, "maximum": 2.0}
        ],
        "nodes": [
            {
                "key": "scope",
                "kind": "object_query",
                "inputs": {"ignored": {"value": None}},
                "outputs": {"objects": "object_set"},
                "config": {"type": "Demand", "select": ["name", "status"]},
            },
            {
                "key": "impact",
                "kind": "function",
                "implementation": {"key": "impact-model", "version": "1.0.0"},
                "inputs": {"objects": {"from": "scope.objects"}},
                "outputs": {"late_orders": "integer"},
            },
        ],
        "edges": [{"source": "scope.objects", "target": "impact.objects"}],
        "metrics": [
            {"key": "late_orders", "type": "integer", "direction": "minimize", "unit": "count", "source": "impact.late_orders"}
        ],
        "budgets": {"max_nodes": 10, "max_objects": 1000, "max_wall_seconds": 60, "max_artifact_bytes": 1048576},
    }


def test_canonical_hash_preserves_list_order_but_not_mapping_order():
    left = {"b": 2, "a": [{"key": "first"}, {"key": "second"}]}
    reordered_map = {"a": [{"key": "first"}, {"key": "second"}], "b": 2}
    reordered_list = {"a": [{"key": "second"}, {"key": "first"}], "b": 2}
    assert stable_hash(left) == stable_hash(reordered_map)
    assert stable_hash(left) != stable_hash(reordered_list)


def test_graph_validation_is_stable_and_rejects_cycles():
    spec = StudySpec.model_validate(spec_payload())
    assert validate_spec(spec) == ["scope", "impact"]
    cyclic = spec_payload()
    cyclic["nodes"][1]["outputs"]["objects"] = "object_set"
    cyclic["edges"].append({"source": "impact.objects", "target": "scope.ignored"})
    with pytest.raises(StudyError, match="acyclic") as error:
        validate_spec(StudySpec.model_validate(cyclic))
    assert error.value.code == "cycle_detected"


def test_structured_json_parameter_is_finite_and_bounded():
    from types import SimpleNamespace

    definition = SimpleNamespace(key="tariff", type="json", minimum=None, maximum=None, enum=None)
    _validate_value(definition, {"Maximum_Load": {"rate": 0.22}}, "parameters.tariff")
    with pytest.raises(StudyError, match="finite JSON"):
        _validate_value(definition, {"rate": float("nan")}, "parameters.tariff")
    with pytest.raises(StudyError, match="64 KiB"):
        _validate_value(definition, {"payload": "x" * 70_000}, "parameters.tariff")


def test_manufacturing_template_library_compiles_and_has_pinned_implementations():
    from app.services.v2.logic_assets import EXECUTORS
    from app.services.v2.scenario_study_templates import templates

    entries = templates()
    assert {entry["template_id"] for entry in entries} == {
        "supply_chain_reasoning", "supplier_disruption", "capacity_replan", "steel_energy_tariff", "cycle_time"
    }
    for entry in entries:
        spec = StudySpec.model_validate(entry["spec"])
        validate_spec(spec)
        for node in spec.nodes:
            if node.implementation and node.kind == "function":
                assert node.implementation.key in EXECUTORS


def test_supplier_outage_study_reports_multilevel_bom_impact_for_baseline_improvement_and_failure():
    from app.services.v2.logic_assets import EXECUTORS
    from app.services.v2.scenario_study_templates import templates

    template = next(item for item in templates() if item["template_id"] == "supplier_disruption")
    parameters = {item["key"]: item["default"] for item in template["spec"]["parameters"]}
    calculate = EXECUTORS["supplier_disruption_impact_v1"]
    baseline = calculate(parameters)
    assert baseline["feasible"] is True
    assert baseline["shortages"] == []

    outage = {**parameters, "supply_options": [
        {**parameters["supply_options"][0], "disabled": True},
        parameters["supply_options"][1],
    ]}
    worsened = {**outage, "supply_options": [
        {**option, "disabled": True} for option in parameters["supply_options"]
    ]}
    recovered = {**outage, "supply_options": [
        {**outage["supply_options"][0], "disabled": True},
        {**outage["supply_options"][1], "quantity": 120},
    ]}

    outage_result = calculate(outage)
    recovery_result = calculate(recovered)
    worsened_result = calculate(worsened)
    assert outage_result["feasible"] is False
    assert outage_result["shortages"] == [{
        "demand_id": "WO-100", "item": "steel_coil", "quantity": 60.0,
        "path": ["pump", "subassembly", "steel_coil"],
    }]
    assert recovery_result["feasible"] is True
    assert recovery_result["shortages"] == []
    assert worsened_result["feasible"] is False
    assert worsened_result["shortages"][0]["quantity"] == 120.0

    cycle = {**parameters, "boms": [
        {"parent_item": "pump", "component_item": "subassembly", "quantity_per": 2},
        {"parent_item": "subassembly", "component_item": "pump", "quantity_per": 1},
    ]}
    with pytest.raises(ValueError, match="BOM cycle"):
        calculate(cycle)


def test_study_publish_case_and_compile_api(client, auth_headers, ontology, db, admin_user):
    ontology_id = ontology["id"]
    created = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions",
        headers=auth_headers,
        json={"name": "Demand and capacity", "draft_spec": spec_payload()},
    )
    assert created.status_code == 201, created.text
    study = created.json()

    validated = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}:validate",
        headers=auth_headers,
    )
    assert validated.status_code == 200
    assert validated.json()["topological_order"] == ["scope", "impact"]

    published = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}:publish",
        headers=auth_headers,
        json={"expected_etag": study["etag"], "change_note": "Initial manufacturing study"},
    )
    assert published.status_code == 201, published.text
    version = published.json()
    assert version["version"] == 1

    view = QueryDataView(
        id="view-study-test",
        ontology_id=ontology_id,
        source_manifest_digest="source-v1",
        metadata_digest="metadata-v1",
        graph_key="graph-study-test",
        status="ready",
        created_by=admin_user.id,
    )
    scenario = ScenarioResource(
        ontology_id=ontology_id,
        owner_id=admin_user.id,
        name="Demand surge sandbox",
        base_view_id=view.id,
    )
    asset = LogicAsset(
        ontology_id=ontology_id,
        asset_key="impact-model",
        name="Impact model",
        kind="python",
        implementation="impact_model",
        version="1.0.0",
        status="published",
    )
    db.add_all([view, scenario, asset])
    db.commit()
    db.refresh(scenario)

    case_response = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases",
        headers=auth_headers,
        json={
            "study_version": 1,
            "scenario_id": scenario.id,
            "name": "Demand +30%",
            "parameter_overrides": {"demand_multiplier": 1.3},
            "ordered_actions": [],
        },
    )
    assert case_response.status_code == 201, case_response.text
    case = case_response.json()
    listed_cases = client.get(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases",
        headers=auth_headers,
        params={"study_version": 1},
    )
    assert listed_cases.status_code == 200
    assert [item["id"] for item in listed_cases.json()["items"]] == [case["id"]]

    compiled = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile",
        headers=auth_headers,
    )
    assert compiled.status_code == 201, compiled.text
    plan = compiled.json()["plan"]
    assert plan["topological_order"] == ["scope", "impact"]
    assert plan["parameters"] == {"demand_multiplier": 1.3}
    assert plan["scenario_revision"] == 0
    assert plan["data_view_id"] == view.id
    assert plan["spec_hash"] == version["spec_hash"]
    assert "ontology_metadata_revision_id" in version

    repeated = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile",
        headers=auth_headers,
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == compiled.json()["id"]

    changed_case = client.patch(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases/{case['id']}",
        headers=auth_headers,
        json={"expected_etag": case["etag"], "parameter_overrides": {"demand_multiplier": 1.4}},
    )
    assert changed_case.status_code == 200, changed_case.text
    assert changed_case.json()["case_hash"] != case["case_hash"]
    recompiled = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile",
        headers=auth_headers,
    )
    assert recompiled.status_code == 201
    assert recompiled.json()["id"] != compiled.json()["id"]
    assert recompiled.json()["plan"]["parameters"]["demand_multiplier"] == 1.4


def test_published_version_is_immutable_when_draft_changes(client, auth_headers, ontology):
    ontology_id = ontology["id"]
    created = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions",
        headers=auth_headers,
        json={"name": "Version test", "draft_spec": spec_payload()},
    ).json()
    first = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{created['id']}:publish",
        headers=auth_headers,
        json={"expected_etag": created["etag"]},
    ).json()

    changed_spec = copy.deepcopy(spec_payload())
    changed_spec["parameters"][0]["default"] = 1.2
    updated = client.patch(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{created['id']}",
        headers=auth_headers,
        json={"expected_etag": 2, "draft_spec": changed_spec},
    )
    assert updated.status_code == 200, updated.text
    old = client.get(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{created['id']}/versions/1",
        headers=auth_headers,
    ).json()
    assert old["spec"]["parameters"][0]["default"] == 1.0
    assert old["spec_hash"] == first["spec_hash"]


def test_compile_rejects_unknown_parameter(client, auth_headers, ontology, db, admin_user):
    ontology_id = ontology["id"]
    study = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions",
        headers=auth_headers,
        json={"name": "Invalid override", "draft_spec": spec_payload()},
    ).json()
    client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}:publish",
        headers=auth_headers,
        json={"expected_etag": 1},
    )
    view = QueryDataView(id="view-invalid-param", ontology_id=ontology_id, source_manifest_digest="s", metadata_digest="m", graph_key="g-invalid-param", status="ready", created_by=admin_user.id)
    scenario = ScenarioResource(ontology_id=ontology_id, owner_id=admin_user.id, name="Invalid", base_view_id=view.id)
    asset = LogicAsset(ontology_id=ontology_id, asset_key="impact-model", name="Impact", kind="python", implementation="impact", version="1.0.0", status="published")
    db.add_all([view, scenario, asset]); db.commit(); db.refresh(scenario)
    case = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases",
        headers=auth_headers,
        json={"study_version": 1, "scenario_id": scenario.id, "name": "Bad", "parameter_overrides": {"not_declared": 2}},
    ).json()
    response = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile",
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_parameter"


def test_run_plan_executes_registered_function_and_materializes_metric(client, auth_headers, ontology, db, admin_user):
    from app.models.v2.scenario import ScenarioRun, ScenarioRunStage
    from app.models.v2.scenario_study import RunPlan
    from app.services.v2.logic_assets import ASSET_DEFINITIONS
    from app.tasks.v2.scenario import _execute_study_run
    from time import monotonic

    ontology_id = ontology["id"]
    definition = next(item for item in ASSET_DEFINITIONS if item["asset_key"] == "cycle_time_v1")
    asset = LogicAsset(
        ontology_id=ontology_id,
        asset_key=definition["asset_key"],
        name=definition["name"],
        kind=definition["kind"],
        implementation=definition["implementation"],
        version=definition.get("version", "1.0.0"),
        input_schema=definition["input_schema"],
        output_schema=definition["output_schema"],
        bindings=definition["bindings"],
        status="published",
    )
    view = QueryDataView(id="view-function-run", ontology_id=ontology_id,
        source_manifest_digest="source-v1", metadata_digest="metadata-v1",
        graph_key="graph-function-run", status="ready", created_by=admin_user.id)
    scenario = ScenarioResource(ontology_id=ontology_id, owner_id=admin_user.id,
        name="Cycle time scenario", base_view_id=view.id)
    db.add_all([asset, view, scenario])
    db.commit()
    db.refresh(scenario)
    study_spec = {
        "parameters": [
            {"key": "setup_seconds", "type": "decimal", "default": 30},
            {"key": "unit_seconds", "type": "decimal", "default": 2},
            {"key": "quantity", "type": "integer", "default": 10},
        ],
        "nodes": [{
            "key": "cycle", "kind": "function",
            "implementation": {"key": "cycle_time_v1", "version": "1.0.0"},
            "inputs": {
                "setup_seconds": {"parameter": "setup_seconds"},
                "unit_seconds": {"parameter": "unit_seconds"},
                "quantity": {"parameter": "quantity"},
            },
            "outputs": {"minutes": "total_minutes"},
        }],
        "metrics": [{"key": "cycle_minutes", "type": "decimal", "direction": "minimize",
                     "unit": "min", "source": "cycle.minutes"}],
        "constraints": [{"key": "cycle_within_shift", "expression_ref": "cycle.minutes",
                         "severity": "hard", "config": {"operator": "lte", "value": 1}}],
    }
    created = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions",
        headers=auth_headers,
        json={"name": "Cycle time", "draft_spec": study_spec},
    ).json()
    published = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{created['id']}:publish",
        headers=auth_headers,
        json={"expected_etag": created["etag"]},
    )
    assert published.status_code == 201, published.text
    case_response = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{created['id']}/cases",
        headers=auth_headers,
        json={"study_version": 1, "scenario_id": scenario.id, "name": "Baseline", "case_kind": "baseline"},
    )
    assert case_response.status_code == 201, case_response.text
    case = case_response.json()
    compile_response = client.post(
        f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions/{created['id']}/cases/{case['id']}:compile",
        headers=auth_headers,
    )
    assert compile_response.status_code == 201, compile_response.text
    plan = db.get(RunPlan, compile_response.json()["id"])
    run = ScenarioRun(
        scenario_id=scenario.id, revision=0, case_key=case["name"],
        logic_asset_key=f"study:{created['id']}", logic_version="scenario-study/v1",
        parameters={"run_plan_id": plan.id}, status="queued", progress=0,
        current_stage="queued", input_digest=plan.plan_hash, study_id=created["id"],
        case_id=case["id"], run_plan_id=plan.id, created_by=admin_user.id,
    )
    db.add(run)
    db.flush()
    db.add(ScenarioRunStage(run_id=run.id, sequence=1, stage_key="node_000"))
    db.commit()
    db.refresh(run)

    _execute_study_run(db, run, monotonic())
    db.refresh(run)
    assert run.status == "completed", run.error_json
    assert run.result_manifest["constraints"][0]["satisfied"] is True
    assert run.result_manifest["constraints_satisfied"] is True
    assert run.result_manifest["metrics"] == [{
        "key": "cycle_minutes", "label": "cycle_minutes", "type": "decimal",
        "direction": "minimize", "unit": "min", "value": 0.8333333333333334,
        "completeness": "exact",
        "provenance": {"node_key": "cycle", "node_digest": run.result_manifest["nodes"]["cycle"]["content_digest"]},
    }]
    assert run.output_digest


def test_published_study_uses_view_catalog_digest_and_rejects_changed_metadata(ontology, db, admin_user):
    from app.models.ontology import OntologyProject
    from app.models.ontology_revision import OntologyRevision
    from app.schemas.v2.scenario_study import CreateCaseRequest
    from app.services.v2.object_query.core import metadata_digest
    from app.services.v2.object_query.metadata import load_sql_metadata
    from app.services.v2.scenario_studies import StudyService

    ontology_id = ontology["id"]
    project = db.get(OntologyProject, ontology_id)
    revision = OntologyRevision(ontology_id=ontology_id, metadata_digest="a" * 64, is_current=True)
    db.add(revision)
    db.flush()
    project.current_revision_id = revision.id
    db.commit()
    service = StudyService(db, ontology_id, admin_user)
    spec = StudySpec.model_validate({
        "nodes": [{"key": "total", "kind": "aggregate_metric", "inputs": {"value": 5},
                   "outputs": {"number": "number"}, "config": {"operation": "sum"}}],
        "metrics": [{"key": "total", "type": "decimal", "source": "total.number"}],
    })
    study = service.create("Catalog digest regression", "", spec)
    version = service.publish(study, expected_etag=study.etag, change_note="", compatibility="compatible")
    catalog_digest = metadata_digest(load_sql_metadata(db, ontology_id))
    assert version.ontology_metadata_revision_id == revision.id
    assert version.ontology_metadata_digest == catalog_digest
    assert version.ontology_metadata_digest != revision.metadata_digest

    view = QueryDataView(id="view-catalog-digest", ontology_id=ontology_id, source_manifest_digest="s",
                         metadata_digest=catalog_digest, graph_key="graph-catalog-digest",
                         status="ready", created_by=admin_user.id)
    db.add(view)
    db.flush()
    scenario = ScenarioResource(ontology_id=ontology_id, owner_id=admin_user.id, name="Catalog snapshot", base_view_id=view.id)
    db.add(scenario)
    db.commit()
    case = service.create_case(study, version, CreateCaseRequest(study_version=version.version,
                              scenario_id=scenario.id, name="Baseline", case_kind="baseline"))
    plan = service.compile(study, case, version)
    assert plan.ontology_metadata_digest == catalog_digest
    assert plan.ontology_metadata_revision_id == revision.id
    view.metadata_digest = "different-catalog"
    db.commit()
    with pytest.raises(StudyError, match="metadata differs") as error:
        service.compile(study, case, version)
    assert error.value.code == "metadata_mismatch"
