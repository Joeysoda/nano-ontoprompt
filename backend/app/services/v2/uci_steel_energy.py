"""Validated, deterministic energy sensitivity calculations for UCI steel data."""
from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Mapping


DATASET_SHA256 = "9b1cee6f9cb9cd9df2b95814ca90a9a2ff15b7f5f1fba0fae3c643e82072eacc"
EXPECTED_ROWS = 35_040
EXPECTED_CATEGORIES = {
    "Light_Load": 18_072,
    "Medium_Load": 9_696,
    "Maximum_Load": 7_272,
}
EXPECTED_USAGE_KWH = {
    "Light_Load": Decimal("155892.81"),
    "Medium_Load": Decimal("372766.54"),
    "Maximum_Load": Decimal("430977.36"),
}
EXPECTED_FIELDS = (
    "date", "Usage_kWh", "Lagging_Current_Reactive.Power_kVarh",
    "Leading_Current_Reactive_Power_kVarh", "CO2(tCO2)",
    "Lagging_Current_Power_Factor", "Leading_Current_Power_Factor", "NSM",
    "WeekStatus", "Day_of_week", "Load_Type",
)


@dataclass(frozen=True)
class EnergyProfile:
    dataset_sha256: str
    row_count: int
    first_timestamp: datetime
    last_timestamp: datetime
    category_counts: Mapping[str, int]
    usage_kwh_by_category: Mapping[str, Decimal]


def reviewed_energy_profile() -> EnergyProfile:
    """Pinned aggregates computed by load_energy_profile from the bundled CSV."""
    return EnergyProfile(
        DATASET_SHA256, EXPECTED_ROWS,
        datetime(2018, 1, 1), datetime(2018, 12, 31, 23, 45),
        EXPECTED_CATEGORIES, EXPECTED_USAGE_KWH,
    )


def load_energy_profile(source: str | Path | bytes) -> EnergyProfile:
    """Load and verify the licensed UCI CSV without relying on row order."""
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError("UCI steel energy CSV checksum does not match the reviewed dataset")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if tuple(reader.fieldnames or ()) != EXPECTED_FIELDS:
        raise ValueError("UCI steel energy CSV header changed")
    rows = list(reader)
    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"UCI steel energy CSV must contain {EXPECTED_ROWS} rows")

    timestamps: list[datetime] = []
    counts: Counter[str] = Counter()
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row_index, row in enumerate(rows, 2):
        if any(value is None or value.strip() == "" for value in row.values()):
            raise ValueError(f"UCI steel energy CSV has a missing value at row {row_index}")
        try:
            timestamps.append(datetime.strptime(row["date"], "%d/%m/%Y %H:%M"))
            usage = Decimal(row["Usage_kWh"])
        except (ValueError, ArithmeticError) as exc:
            raise ValueError(f"UCI steel energy CSV has an invalid value at row {row_index}") from exc
        if not usage.is_finite() or usage < 0:
            raise ValueError(f"UCI steel energy CSV has invalid energy at row {row_index}")
        category = row["Load_Type"]
        counts[category] += 1
        totals[category] += usage

    ordered = sorted(timestamps)
    if len(set(ordered)) != EXPECTED_ROWS:
        raise ValueError("UCI steel energy timestamps must be unique")
    if any(right - left != timedelta(minutes=15) for left, right in zip(ordered, ordered[1:])):
        raise ValueError("UCI steel energy timestamps must form a complete 15-minute series")
    if ordered[0] != datetime(2018, 1, 1) or ordered[-1] != datetime(2018, 12, 31, 23, 45):
        raise ValueError("UCI steel energy timestamp coverage changed")
    if dict(counts) != EXPECTED_CATEGORIES or dict(totals) != EXPECTED_USAGE_KWH:
        raise ValueError("UCI steel energy known-answer aggregates changed")
    return EnergyProfile(digest, len(rows), ordered[0], ordered[-1], dict(counts), dict(totals))


def tariff_sensitivity(
    profile: EnergyProfile,
    rates_per_kwh: Mapping[str, Decimal | str | int | float],
    *,
    maximum_load_rate_change: Decimal | str | int | float,
) -> dict:
    """Compare illustrative per-kWh tariffs while keeping measured energy fixed."""
    change = Decimal(str(maximum_load_rate_change))
    if not change.is_finite() or change < Decimal(-1):
        raise ValueError("maximum-load rate change must be finite and at least -100%")
    normalized = {key: Decimal(str(value)) for key, value in rates_per_kwh.items()}
    if set(normalized) != set(EXPECTED_CATEGORIES) or any(not value.is_finite() or value < 0 for value in normalized.values()):
        raise ValueError("provide non-negative rates for all three load categories")
    baseline = sum(profile.usage_kwh_by_category[key] * normalized[key] for key in EXPECTED_CATEGORIES)
    candidate_rates = dict(normalized)
    candidate_rates["Maximum_Load"] *= Decimal(1) + change
    candidate = sum(profile.usage_kwh_by_category[key] * candidate_rates[key] for key in EXPECTED_CATEGORIES)
    energy_total = sum(profile.usage_kwh_by_category.values())
    return {
        "baseline_cost": str(baseline.quantize(Decimal("0.0001"))),
        "candidate_cost": str(candidate.quantize(Decimal("0.0001"))),
        "cost_delta": str((candidate - baseline).quantize(Decimal("0.0001"))),
        "usage_kwh_before": str(energy_total),
        "usage_kwh_after": str(energy_total),
        "maximum_load_rate_change": str(change),
        "rates_are_illustrative": True,
        "dataset_sha256": profile.dataset_sha256,
    }
