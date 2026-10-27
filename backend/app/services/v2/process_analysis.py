"""Domain-neutral event-log analysis for operational process evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from math import isfinite


class ProcessAnalysisError(ValueError):
    pass


def _time(value) -> float:
    if isinstance(value, bool):
        raise ProcessAnalysisError("timestamp must be ISO-8601 or numeric")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError as exc:
            raise ProcessAnalysisError("timestamp must be ISO-8601 or numeric") from exc
    else:
        raise ProcessAnalysisError("timestamp must be ISO-8601 or numeric")
    if not isfinite(result):
        raise ProcessAnalysisError("timestamp must be finite")
    return result


def analyze(events):
    if not isinstance(events, list) or not 1 <= len(events) <= 100_000:
        raise ProcessAnalysisError("events must contain 1..100000 rows")
    cases = defaultdict(list)
    seen_ids = set()
    for index, row in enumerate(events):
        if not isinstance(row, dict):
            raise ProcessAnalysisError("event must be an object")
        case_id, activity = str(row.get("case_id", "")).strip(), str(row.get("activity", "")).strip()
        if not case_id or not activity:
            raise ProcessAnalysisError("case_id and activity are required")
        event_id = str(row.get("event_id", index))
        if event_id in seen_ids:
            raise ProcessAnalysisError("event_id must be unique")
        seen_ids.add(event_id)
        cases[case_id].append((_time(row.get("timestamp")), index, activity))

    transitions, dwell, variants, rework_cases = Counter(), defaultdict(list), Counter(), 0
    durations = []
    for rows in cases.values():
        rows.sort(key=lambda item: (item[0], item[1]))
        activities = [item[2] for item in rows]
        variants[tuple(activities)] += 1
        rework_cases += int(len(set(activities)) != len(activities))
        durations.append(rows[-1][0] - rows[0][0])
        for current, following in zip(rows, rows[1:]):
            elapsed = following[0] - current[0]
            if elapsed < 0:
                raise ProcessAnalysisError("events cannot move backwards in time")
            transitions[(current[2], following[2])] += 1
            dwell[current[2]].append(elapsed)

    return {
        "case_count": len(cases), "event_count": len(events), "rework_case_count": rework_cases,
        "average_case_duration": sum(durations) / len(durations),
        "transitions": [{"source": source, "target": target, "count": count}
                        for (source, target), count in sorted(transitions.items())],
        "activity_metrics": [{"activity": activity, "occurrences_with_next": len(values),
                              "average_dwell": sum(values) / len(values), "maximum_dwell": max(values)}
                             for activity, values in sorted(dwell.items())],
        "variants": [{"activities": list(path), "case_count": count}
                     for path, count in sorted(variants.items(), key=lambda item: (-item[1], item[0]))[:100]],
        "complete": len(variants) <= 100,
    }
