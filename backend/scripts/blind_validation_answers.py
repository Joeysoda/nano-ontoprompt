"""Compute sealed known answers for the Plan B blind validation kit.

Answers are derived from the pinned datasets, never typed by hand, so the
facilitator can verify a participant's answer without the implementer
having written it into any prompt.  Usage:

    python scripts/blind_validation_answers.py --pdm <dir> --out answers.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))


def pdm_answers(directory: Path) -> dict:
    from v2.test_machinery_azure_pdm import RULES, oracle  # same oracle the acceptance test uses
    failures = pd.read_csv(directory / "PdM_failures.csv", parse_dates=["datetime"])
    maint = pd.read_csv(directory / "PdM_maint.csv", parse_dates=["datetime"])
    machines = pd.read_csv(directory / "PdM_machines.csv")
    telemetry = pd.read_csv(directory / "PdM_telemetry.csv", parse_dates=["datetime"]).sort_values(["machineID", "datetime"]).reset_index(drop=True)
    telemetry["utc"] = telemetry["datetime"].dt.tz_localize("UTC")
    comp4 = failures[failures.failure == "comp4"].machineID.value_counts()
    top = comp4[comp4 == comp4.max()].index.tolist()
    last17 = maint[maint.machineID == 17].sort_values("datetime").iloc[-1]
    episodes = {}
    for channel in ("vibration", "volt"):
        _, found = oracle(telemetry, channel, RULES[channel][0])
        episodes[channel] = pd.Series([m for m, _, _ in found]).value_counts()
    vib = episodes["vibration"]
    return {
        "Q1": {"answer": [f"machine-{m:03d}" for m in sorted(top)], "count": int(comp4.max())},
        "Q2": {"date": str(last17.datetime.date()), "component": last17.comp},
        "Q3": {"answer": int((machines.model == "model3").sum())},
        "Q4": {"answer": sorted(vib[vib == vib.max()].index.tolist()), "episodes": int(vib.max())},
        "Q5": {"answer": int(episodes["volt"].get("machine-099", 0)), "note": "open alerts right after the seeded evaluation; changes once anyone resolves them"},
        "Q6": {"check": "the chosen machine is the Q4 answer and its maintenance_status becomes 'scheduled' through an Action run"},
    }


def frepple_answers() -> dict:
    from test_solver_functions import frepple_problem, run
    base = run(frepple_problem(capacity_hours=120.0))
    by_demand = {a["demand"]: a["operator"] for a in base["assignments"]}
    varnishers = sorted({i for (t, i), p in frepple_problem().objects.items() if t == "Operator" and "Varnish skill" in p["skills"]})
    return {
        "Q7": {"answer": varnishers},
        "Q8": {"answer": base["loads"]["Carl"], "makespan": base["solver"]["objective"]},
        "Q9": {"answer": "infeasible", "reason": "460 h of work exceeds 3 x 120 h"},
        "Q10": {"live": None, "scenario": by_demand["Demand 07"]},
    }


def steel_answers() -> dict:
    from test_rule_runtime_steel import CSV, oracle_episodes
    df = pd.read_csv(CSV, encoding="utf-8-sig")
    ts = pd.to_datetime(df["date"], format="%d/%m/%Y %H:%M")
    df["ts"] = ts.where(df["NSM"] != 0, ts + pd.Timedelta(days=1)).dt.tz_localize("Asia/Seoul")
    df["utc"] = df["ts"].dt.tz_convert("UTC")
    days = df.assign(day=ts.dt.date).groupby("day").agg(total=("Usage_kWh", "sum"), week=("WeekStatus", "first"))
    weekend = days[days.week == "Weekend"]
    threshold = float(df["Usage_kWh"].quantile(0.95))
    return {
        "Q11": {"answer": int((weekend.total.round(2) > round(float(weekend.total.median()), 2)).sum())},
        "Q12": {"answer": len(oracle_episodes(df, threshold)), "threshold_kwh": threshold},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdm", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    answers = {**pdm_answers(args.pdm), **frepple_answers(), **steel_answers()}
    args.out.write_text(json.dumps(answers, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print(f"wrote {len(answers)} answers to {args.out}")


if __name__ == "__main__":
    main()
