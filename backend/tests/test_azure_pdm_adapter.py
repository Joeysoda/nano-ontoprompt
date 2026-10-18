"""Azure PdM batch adapter contract on small hand-made files."""
import pytest

from app.services.v2.azure_pdm import PdmError, load_surface, series_id

HEADER = {
    "PdM_machines.csv": '"machineID","model","age"\n',
    "PdM_failures.csv": '"datetime","machineID","failure"\n',
    "PdM_maint.csv": '"datetime","machineID","comp"\n',
    "PdM_errors.csv": '"datetime","machineID","errorID"\n',
    "PdM_telemetry.csv": '"datetime","machineID","volt","rotate","pressure","vibration"\n',
}


def write(tmp_path, **bodies):
    for name, header in HEADER.items():
        (tmp_path / name).write_text(header + bodies.get(name, ""), encoding="utf-8")
    return tmp_path


def base(tmp_path, **extra):
    return write(tmp_path, **{
        "PdM_machines.csv": '2,"model4",7\n1,"model3",18\n',
        "PdM_failures.csv": '2015-01-05 06:00:00,1,"comp4"\n2015-01-05 06:00:00,9,"comp1"\n',
        "PdM_maint.csv": '2015-01-01 06:00:00,2,"comp2"\n',
        "PdM_telemetry.csv": "2015-01-01 07:00:00,1,1,2,3,4\n2015-01-01 06:00:00,1,5,6,7,8\n2015-01-01 06:00:00,2,1,1,1,1\n",
        **extra})


def test_surface_is_canonical_and_ordered(tmp_path):
    surface = load_surface(base(tmp_path))
    assert [m["id"] for m in surface.machines] == ["machine-001", "machine-002"]
    assert surface.machines[0]["properties"]["vibration_series"] == series_id("machine-001", "vibration")
    # Events for machines outside the registry (machine 9) are not invented as objects.
    assert sorted(n["entity_type"] for n in surface.event_nodes) == ["FailureEvent", "MaintenanceEvent"]
    failure = next(n for n in surface.event_nodes if n["entity_type"] == "FailureEvent")
    assert failure["properties"]["start_timestamp"] == "2015-01-05T06:00:00+00:00" == failure["properties"]["end_timestamp"]
    assert {"source": failure["id"], "target": "machine-001", "type": "FAILURE_OF"} in surface.event_links
    volt = next(s for s in surface.syncs if s["series_id"] == series_id("machine-001", "volt"))
    assert [p["value"] for p in volt["points"]] == [5.0, 1.0] and volt["unit"] == "V"
    assert surface.manifest["counts"] == {"machines": 2, "events": 2, "series": 8, "points": 12}
    assert all(f["sha256"] and f["rows"] >= 0 for f in surface.manifest["files"])


def test_machine_limit_restricts_every_surface(tmp_path):
    surface = load_surface(base(tmp_path), machine_limit=1)
    assert {s["root_id"] for s in surface.syncs} == {"machine-001"}
    assert all(link["target"] == "machine-001" for link in surface.event_links)


@pytest.mark.parametrize("name,body,message", [
    ("PdM_telemetry.csv", "2015-01-01 06:00:00,1,1,2,3,4\n2015-01-01 06:00:00,1,1,2,3,4\n", "duplicate timestamps"),
    ("PdM_telemetry.csv", "2015-01-01 06:00:00,1,nan,2,3,4\n", "non-finite"),
    ("PdM_failures.csv", "2015-01-05 06:00:00,1,comp4\n2015-01-05 06:00:00,1,comp4\n", "duplicate event"),
    ("PdM_maint.csv", "05/01/2015 06:00,1,comp4\n", "invalid timestamp"),
])
def test_invalid_source_rows_fail_explicitly(tmp_path, name, body, message):
    with pytest.raises(PdmError, match=message):
        load_surface(base(tmp_path, **{name: body}))
