"""High-volume contract tests for the six local heterogeneous logic assets."""
import pytest
from app.services.v2.logic_assets import execute

def asset(key): return {"asset_key":key,"implementation":key,"version":"1.0.0"}

def test_business_release_gate_samples():
    for i in range(240):
        x={"tool_condition":"good" if i%5 else "worn","visual_inspection":"pass","machining_finalized":i%7 != 0,"clamp_pressure":40 if i%11 else 35}
        out=execute(asset("release_gate_v1"),x)["output"]
        assert out["decision"] == ("release" if i%5 and i%7 and i%11 else "hold")

def test_math_cycle_samples():
    for i in range(300):
        out=execute(asset("cycle_time_v1"),{"setup_seconds":10+i%30,"unit_seconds":2+i%9,"quantity":i%20})["output"]
        assert out["total_seconds"] == 10+i%30+(2+i%9)*(i%20)

def test_statistics_window_samples():
    for i in range(250):
        values=[float((j*3+i)%17) for j in range(5+(i%20))]
        out=execute(asset("observation_summary_v1"),{"values":values,"anomaly_threshold":8})["output"]
        assert out["count"]==len(values) and out["minimum"]==min(values) and out["maximum"]==max(values)

def test_ml_risk_samples():
    for i in range(500):
        x={"temperature":55+(i%60),"vibration":0.5+(i%50)/10,"pressure":10-(i%8)/4}
        out=execute(asset("tool_wear_risk_v1"),x)["output"]
        assert 0 <= out["score"] <= 1 and out["risk_level"] in {"low","medium","high"}
        assert execute(asset("tool_wear_risk_v1"),x)["output"]==out

def test_optimization_samples():
    for i in range(200):
        tasks=[{"id":f"T{j}","duration":1+(i+j)%4,"eligible_resources":["R1","R2"]} for j in range(3)]
        out=execute(asset("resource_assignment_v1"),{"tasks":tasks,"resources":[{"id":"R1","capacity":99},{"id":"R2","capacity":99}]})["output"]
        assert out["feasible"] and len(out["assignments"])==3
        assert all(a["resource_id"] in {"R1","R2"} for a in out["assignments"])

def test_planning_samples():
    for i in range(200):
        tasks=[{"id":"cut","duration":2,"priority":1,"resource":"machine"},{"id":"finish","duration":1+(i%3),"priority":2,"resource":"machine","predecessors":["cut"]},{"id":"inspect","duration":1,"priority":3,"resource":"qa","predecessors":["finish"]}]
        out=execute(asset("priority_plan_v1"),{"tasks":tasks})["output"]
        assert out["feasible"] and out["makespan"]>=4
        pos={s["task_id"]:s for s in out["schedule"]}
        assert pos["cut"]["end"] <= pos["finish"]["start"] <= pos["inspect"]["start"]

def test_shared_contract_errors_and_trace():
    cases=[("release_gate_v1",{}),("cycle_time_v1",{}),("observation_summary_v1",{"values":[]}),("tool_wear_risk_v1",{}),("resource_assignment_v1",{"tasks":[],"resources":[]}),("priority_plan_v1",{"tasks":[{"id":"a","predecessors":["missing"]}]})]
    for key, data in cases:
        with pytest.raises(ValueError): execute(asset(key), data)
    result=execute(asset("cycle_time_v1"),{"setup_seconds":1,"unit_seconds":2,"quantity":3})
    assert result["version"]=="1.0.0" and [x["stage"] for x in result["trace"]]==["bind","execute","validate"]
