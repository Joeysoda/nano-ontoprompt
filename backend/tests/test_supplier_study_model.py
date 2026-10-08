"""Known-answer checks against the pinned public frePPLe fixture."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.v2.manufacturing_data import adapt
from app.services.v2.supplier_action import compile_supplier_action
from app.services.v2.supplier_resilience import calculate, validate_profile
from app.routers.v2.supplier_studies import _compatibility

ROOT = Path("/state") if Path("/state/manufacturing_demo.json").exists() else Path(__file__).resolve().parents[2] / "data" / "frepple_demo"


@pytest.fixture(scope="module")
def prepared():
    report = adapt((ROOT / "manufacturing_demo.json").read_bytes())
    manifest = json.loads((ROOT / "whatif_synthetic_manifest.json").read_text(encoding="utf-8"))
    snapshot = SimpleNamespace(objects={(node["entity_type"], node["id"]): node["properties"]
                                        for node in report["nodes"]})
    return report, manifest, snapshot


def test_golden_baseline_b_c(prepared):
    _, manifest, snapshot = prepared
    expected = {"baseline": (62.5, 0, 22500), "supplier_b": (43.75, 220, 10260),
                "supplier_c": (68.75, 0, 14100)}
    for key, (on_time, shortage, cost) in expected.items():
        result = calculate(snapshot, manifest["profiles"][key], manifest["scenario_time"])
        metrics = {metric["key"]: metric["value"] for metric in result["metrics"]}
        assert len(result["demand_impacts"]) == 16
        assert metrics["on_time_delivery_rate"] == on_time
        assert metrics["shortage_quantity"] == shortage
        assert metrics["estimated_procurement_cost"] == cost
        assert result["completeness"] == "complete"


def test_action_targets_are_server_derived_and_typed(prepared):
    report, manifest, snapshot = prepared
    scenario = SimpleNamespace(id="candidate-scenario", head_revision=0, etag=1)
    request = compile_supplier_action(report["ontology_id"], scenario, snapshot,
                                      manifest["profiles"]["supplier_b"], "idempotent-request")
    assert len(request.edits) == 12
    assert [edit.op for edit in request.edits].count("set_property") == 2
    assert [edit.op for edit in request.edits].count("create_object") == 3
    assert all(edit.source_action == "replace_wood_supplier_v1" for edit in request.edits)
    assert request.edits[1].target.object_id.startswith("synthetic:")


def test_invalid_profile_rejected(prepared):
    _, manifest, _ = prepared
    profile = {**manifest["profiles"]["supplier_b"], "capacity": {"wooden beam": -1, "wooden panel": 10}}
    with pytest.raises(ValueError, match="invalid capacity"):
        validate_profile(profile)


def test_registered_logic_asset_matches_pure_model(prepared):
    from app.services.v2.logic_assets import SUPPLIER_RESILIENCE_ASSET, execute
    _, manifest, snapshot = prepared
    inputs = {"objects": [{"type": typ, "id": oid, "properties": props}
        for (typ, oid), props in snapshot.objects.items()], "profile": manifest["profiles"]["supplier_b"],
        "scenario_time": manifest["scenario_time"], "scope_items": manifest["scope"]["items"]}
    first = execute(SUPPLIER_RESILIENCE_ASSET, inputs)
    second = execute(SUPPLIER_RESILIENCE_ASSET, inputs)
    assert first == second
    assert first["version"] == "2.1.0"
    assert first["output"] == calculate(snapshot, inputs["profile"], inputs["scenario_time"], inputs["scope_items"])


def test_protected_action_fields_rejected(prepared):
    _, manifest, _ = prepared
    profile = {**manifest["profiles"]["supplier_b"], "object_id": "forged-node"}
    with pytest.raises(ValueError, match="protected"):
        validate_profile(profile)


def test_compatibility_changes_with_time_scope_and_projection():
    view = SimpleNamespace(content_digest="pinned-view")
    study = SimpleNamespace(source_manifest_digest="fixture", scenario_time="2021-01-01T00:00:00",
                            scope_hash="both-wood", smoothing_minutes=0, model_key="supplier_resilience_v2",
                            model_version="2.1.0", model_config_alias="bounded_wood_supply",
                            parameter_projection=["capacity"])
    original = _compatibility(study, view)
    study.scenario_time = "2021-01-02T00:00:00"
    assert _compatibility(study, view) != original
    study.scenario_time = "2021-01-01T00:00:00"
    study.scope_hash = "beam-only"
    assert _compatibility(study, view) != original
    study.scope_hash = "both-wood"
    study.parameter_projection = ["lead_days"]
    assert _compatibility(study, view) != original


def test_candidate_case_can_be_created_renamed_and_archived(db, admin_user, prepared):
    from app.models.ontology import OntologyProject
    from app.models.v2.query_view import QueryDataView
    from app.models.v2.scenario import ScenarioResource, ScenarioStudy, ScenarioStudyCase
    from app.routers.v2.supplier_studies import (ArchiveCase, CreateCase, RenameCase,
                                                  _view, archive_case, create_case, rename_case)
    from app.services.v2.object_query.normalize import stable_hash

    _, manifest, _ = prepared
    ontology = OntologyProject(name="Supplier test", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology); db.flush()
    view = QueryDataView(ontology_id=ontology.id, source_manifest_digest="fixture",
                         metadata_digest="metadata", content_digest="base", graph_key="test_supplier_view",
                         status="ready", created_by=admin_user.id)
    db.add(view); db.flush()
    study = ScenarioStudy(ontology_id=ontology.id, owner_id=admin_user.id, name="Study",
                          base_view_id=view.id, source_manifest_digest="fixture",
                          scenario_time=manifest["scenario_time"], scope_definition=manifest["scope"],
                          scope_hash=stable_hash(manifest["scope"]), model_key="supplier_resilience_v2",
                          model_version="2.1.0", model_config_alias="bounded_wood_supply",
                          parameter_projection=["capacity"])
    db.add(study); db.flush()
    for index, key in enumerate(("baseline", "supplier_b")):
        scenario = ScenarioResource(ontology_id=ontology.id, owner_id=admin_user.id,
                                    name=key, base_view_id=view.id, protected_demo=True)
        db.add(scenario); db.flush()
        profile = manifest["profiles"][key]
        db.add(ScenarioStudyCase(study_id=study.id, scenario_id=scenario.id, case_key=key,
                                 display_name=key, case_kind="baseline" if index == 0 else "candidate",
                                 sort_order=index, submitted_parameters=profile,
                                 parameters_hash=stable_hash(profile), synthetic_manifest_hash="fixture"))
    db.commit()

    created = create_case(ontology.id, study.id, CreateCase(name="Supplier D"), db, admin_user)
    assert created["name"] == "Supplier D"
    assert created["scenario_revision"] == 0
    assert created["parameters"] == manifest["profiles"]["supplier_b"]
    assert created["protected"] is False
    renamed = rename_case(ontology.id, study.id, created["id"],
                          RenameCase(etag=created["etag"], name="Supplier D revised"), db, admin_user)
    assert renamed["name"] == "Supplier D revised"
    archived = archive_case(ontology.id, study.id, created["id"],
                            ArchiveCase(etag=renamed["etag"]), db, admin_user)
    assert archived["status"] == "archived"
    assert len(_view(db, study)["cases"]) == 2
