"""Planning dataset for the MRPSpeed-style demo: real demand, rule-derived supply.

Real (DataCo Smart Supply Chain, Mendeley Data doi:10.17632/8gx2fvg2k6.5,
CC BY 4.0, SHA-256 fa6d022e...0aa6): products, list prices, order dates,
quantities, markets, customer segments and scheduled shipping days for five
high-volume assembled products.  Cancelled and suspected-fraud orders are
dropped; customer identity columns are never read.

Notional (no public source exists): BOMs, on-hand stock, purchase orders,
line capacity, one supplier disruption and one quality hold.  They are
derived by fixed rules from the real demand (e.g. PO volume = real demand x
coverage factor) with a fixed seed, so the dataset is reproducible.  The
planning model computes every KPI, risk, coverage cell and action from these
inputs; nothing here encodes an answer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
import random
from pathlib import Path

SEED = 20161003
PLANNING_DATE = date(2016, 10, 3)          # a Monday inside the real data window
HORIZON_WEEKS = 52
QUARTER = (date(2016, 10, 3), date(2017, 1, 2))
SITES = ("Plant A", "Plant B")
DATASET = Path(__file__).resolve().parents[3] / "data" / "mrpspeed_demo" / "mrpspeed_demo.json"

# Real DataCo product -> notional line code and BOM. Part: (name, qty per unit, share of price, lead days).
PRODUCTS = {
    "Diamondback Women's Serene Classic Comfort Bi": ("BIKE", "Comfort Bike", {
        "Frame Module": [("Aluminum Frame", 1, .12, 42), ("Suspension Fork", 1, .05, 28), ("Headset", 1, .01, 21)],
        "Drivetrain Module": [("Crankset", 1, .03, 28), ("Chain", 1, .01, 14), ("Rear Derailleur", 1, .03, 35), ("Shifter", 1, .02, 28), ("Pedal", 2, .005, 14)],
        "Wheel Module": [("Wheelset", 1, .07, 35), ("Tire", 2, .01, 21), ("Inner Tube", 2, .003, 14)],
        "Cockpit Module": [("Handlebar", 1, .01, 21), ("Grip", 2, .002, 14), ("Saddle", 1, .015, 21), ("Brake Set", 1, .025, 28)],
        "Pack Module": [("Shipping Carton", 1, .006, 14), ("Hardware Kit", 1, .004, 14)]}),
    "Field & Stream Sportsman 16 Gun Fire Safe": ("SAFE", "Gun Fire Safe", {
        "Body Module": [("Steel Body Panel", 1, .14, 35), ("Door Panel", 1, .07, 35), ("Fireboard Lining", 4, .02, 28), ("Hinge Set", 1, .01, 21)],
        "Lock Module": [("Combination Lock", 1, .05, 42), ("Locking Bolt", 6, .004, 21), ("Handle", 1, .01, 21)],
        "Interior Module": [("Gun Rack", 1, .02, 21), ("Interior Carpet", 1, .01, 14)],
        "Pack Module": [("Shipping Crate", 1, .01, 14), ("Hardware Kit", 1, .004, 14)]}),
    "Pelican Sunstream 100 Kayak": ("KAYAK", "Sit-in Kayak", {
        "Hull Module": [("HDPE Hull", 1, .2, 42), ("Drain Plug", 1, .002, 14), ("Deck Bungee", 1, .004, 14)],
        "Outfit Module": [("Seat", 1, .04, 28), ("Footrest", 2, .006, 21), ("Carry Handle", 2, .003, 14), ("Paddle", 1, .03, 21)],
        "Pack Module": [("Shipping Carton", 1, .01, 14)]}),
    "Perfect Fitness Perfect Rip Deck": ("RIP", "Rip Deck Trainer", {
        "Frame Module": [("Steel Rail", 1, .12, 28), ("Bushing", 4, .006, 14), ("Wheel", 4, .01, 21)],
        "Grip Module": [("Handle Grip", 2, .02, 14), ("Hardware Kit", 1, .004, 14)],
        "Pack Module": [("Shipping Carton", 1, .02, 14)]}),
    "O'Brien Men's Neoprene Life Vest": ("VEST", "Neoprene Life Vest", {
        "Shell Module": [("Neoprene Shell", 1, .12, 28), ("Foam Panel", 4, .015, 21), ("Zipper", 1, .01, 21)],
        "Strap Module": [("Buckle", 3, .006, 14), ("Webbing", 1, .01, 14), ("Safety Label", 1, .002, 14)],
        "Pack Module": [("Poly Bag", 1, .003, 14)]}),
}
SHARED = {"Shipping Carton", "Hardware Kit"}
DISRUPTED = {("BIKE", "Aluminum Frame"), ("BIKE", "Rear Derailleur"), ("SAFE", "Combination Lock")}
REWORK = {("BIKE", "Rear Derailleur"): "Returned Derailleur"}        # refurbish returned parts
QUALITY_HOLD = ("SAFE", 14, 56)                                      # fire-rating retest blocks the line (days)
POLICIES = {
    "MRPSpeed Optimized": {"capacity_factor": 1.0, "pull_in_days": 0, "conversion_factor": 1.0, "exclude_low_confidence": False},
    "MRPSpeed Cash Preserving": {"capacity_factor": 0.85, "pull_in_days": 0, "conversion_factor": 0.92, "exclude_low_confidence": False},
    "MRPSpeed Grey Swan": {"capacity_factor": 1.0, "pull_in_days": 0, "conversion_factor": 1.0, "exclude_low_confidence": True},
    "MRPSpeed Max": {"capacity_factor": 1.4, "pull_in_days": 30, "conversion_factor": 1.18, "exclude_low_confidence": False},
}
INITIAL_POLICY = {"BIKE": "MRPSpeed Optimized", "SAFE": "MRPSpeed Optimized", "KAYAK": "MRPSpeed Optimized",
                  "RIP": "MRPSpeed Cash Preserving", "VEST": "MRPSpeed Grey Swan"}
SEGMENT_PRIORITY = {"Corporate": 3, "Home Office": 2, "Consumer": 1}


def _day(value: date) -> int:
    return (value - PLANNING_DATE).days


def build(csv_path) -> dict:
    import pandas as pd

    columns = ["Product Name", "Product Price", "Order Item Quantity", "order date (DateOrders)", "Market",
               "Days for shipment (scheduled)", "Order Status", "Customer Segment"]
    frame = pd.read_csv(csv_path, encoding="latin-1", usecols=columns)
    frame = frame[frame["Product Name"].isin(PRODUCTS) & ~frame["Order Status"].isin(["CANCELED", "SUSPECTED_FRAUD"])].copy()
    frame["due"] = pd.to_datetime(frame["order date (DateOrders)"]).dt.normalize() + pd.to_timedelta(frame["Days for shipment (scheduled)"], unit="D")
    end = PLANNING_DATE + timedelta(weeks=HORIZON_WEEKS)
    frame = frame[(frame["due"].dt.date >= PLANNING_DATE) & (frame["due"].dt.date < end)]
    frame["month"] = frame["due"].dt.to_period("M")
    # One demand event = one product, one market, one month of real orders.
    grouped = frame.groupby(["Product Name", "Market", "month"]).agg(
        quantity=("Order Item Quantity", "sum"), due=("due", "max"), price=("Product Price", "first"),
        orders=("Order Item Quantity", "size"), segment=("Customer Segment", lambda s: s.mode().iloc[0]))
    rng = random.Random(SEED)
    lines, items, boms, demands, inventory, commitments, options = [], [], [], [], [], [], []
    weekly = frame.groupby("Product Name")["Order Item Quantity"].sum() / HORIZON_WEEKS
    for product, (code, label, modules) in PRODUCTS.items():
        price = float(frame.loc[frame["Product Name"] == product, "Product Price"].iloc[0])
        rate = round(float(weekly[product]) * 1.15, 1)                 # lines are planned 15% above average demand
        lines.append({"id": code, "name": label, "source_product": product, "policy": INITIAL_POLICY[code], "unit_price": round(price, 2),
                      "weekly_rate": rate, "conversion_cost": round(price * 0.12, 2), "resource": f"{code} line"})
        top = f"{code} Assembly"
        items.append({"id": top, "kind": "assembly", "product_line": code, "unit_cost": 0})
        for module, parts in modules.items():
            module_id = f"{code}:{module}"
            items.append({"id": module_id, "kind": "module", "product_line": code, "unit_cost": 0})
            boms.append({"parent_item": top, "component_item": module_id, "quantity_per": 1})
            for name, qty, share, lead in parts:
                pid = name if name in SHARED else f"{code}:{name}"
                boms.append({"parent_item": module_id, "component_item": pid, "quantity_per": qty})
                if not any(row["id"] == pid for row in items):
                    items.append({"id": pid, "kind": "part", "name": name, "product_line": None if name in SHARED else code,
                                  "unit_cost": round(price * share, 2), "lead_time_days": lead, "critical": (code, name) in DISRUPTED})
    for (product, market, month), row in grouped.iterrows():
        code = PRODUCTS[product][0]
        line = next(item for item in lines if item["id"] == code)
        demands.append({"id": f"DE-{code}-{market[:3].upper()}-{month.strftime('%y%m')}", "item": f"{code} Assembly", "product_line": code,
                        "customer": market, "quantity": int(row["quantity"]), "due_at": _day(row["due"].date()),
                        "priority": SEGMENT_PRIORITY.get(row["segment"], 1), "unit_revenue": round(float(row["price"]), 2),
                        "processing_days": round(int(row["quantity"]) / line["weekly_rate"] * 7, 2), "resource": f"{code} line",
                        "source_orders": int(row["orders"])})
    demands.sort(key=lambda row: (row["due_at"], row["id"]))

    child = defaultdict(list)
    for row in boms:
        child[row["parent_item"]].append((row["component_item"], row["quantity_per"]))

    def leaves(item, qty):
        if item not in child:
            yield item, qty
            return
        for comp, per in child[item]:
            yield from leaves(comp, qty * per)

    need = defaultdict(float)
    for demand in demands:
        for pid, qty in leaves(demand["item"], demand["quantity"]):
            need[pid] += qty
    cost = {row["id"]: row for row in items}
    for pid, total in sorted(need.items()):
        code, name = pid.split(":", 1) if ":" in pid else (None, pid)
        disrupted = (code, name) in DISRUPTED
        cover = 0.92 if disrupted else rng.uniform(1.05, 1.2)
        onhand = int(total * (0.03 if disrupted else rng.uniform(0.08, 0.16)))
        for index, site in enumerate(SITES):
            inventory.append({"id": f"LOT-{pid}-{site}", "item": pid, "location": site, "quantity": onhand // 2 if index == 0 else onhand - onhand // 2})
        remaining, pos = max(0, int(total * cover) - onhand), rng.randrange(10, 16)
        for po in range(pos):
            arrive = 7 + po * (HORIZON_WEEKS * 7 - 21) // pos + rng.randrange(0, 5)
            late = disrupted and arrive < 16 * 7      # the disruption hits the next ~16 weeks of deliveries
            row = {"id": f"PO-{len(commitments) + 1:05d}", "item": pid, "quantity": max(1, remaining // pos),
                   "supplier": "Summit Metals" if disrupted else rng.choice(("Northwind Components", "Apex Precision", "Coastal Plastics")),
                   "available_at": arrive + (35 if late else 0), "status": "active",
                   "unit_cost": cost[pid]["unit_cost"], "confidence": 0.6 if late else 0.95}
            if late:
                row.update(expedite_available_at=arrive, expedite_unit_premium=round(cost[pid]["unit_cost"] * 0.3, 2))
            commitments.append(row)
        if (code, name) in REWORK:
            donor = f"{code}:{REWORK[(code, name)]}"
            items.append({"id": donor, "kind": "part", "name": REWORK[(code, name)], "product_line": code, "unit_cost": 0, "lead_time_days": 0})
            inventory.append({"id": f"LOT-{donor}-returns", "item": donor, "location": SITES[0], "quantity": int(total * 0.15)})
            options.append({"id": f"RW-{pid}", "supplier": "In-house rework", "item": pid, "quantity": int(total * 0.15), "lead_time": 10,
                            "unit_cost": round(cost[pid]["unit_cost"] * 0.35, 2), "qualified": True, "consumes_item": donor})
    hold_line, hold_start, hold_end = QUALITY_HOLD
    return {"scenario_time": f"{PLANNING_DATE.isoformat()}T00:00:00Z", "horizon_weeks": HORIZON_WEEKS,
            "quarter_start": _day(QUARTER[0]), "quarter_end": _day(QUARTER[1]),
            "product_lines": lines, "policy_definitions": [{"id": key, **value} for key, value in POLICIES.items()],
            "items": items, "demands": demands, "boms": boms, "inventory": inventory, "reservations": [],
            "supply_commitments": commitments, "supply_options": options,
            "fixed_tasks": [{"id": f"HOLD-{hold_line}-RETEST", "resource": f"{hold_line} line", "start": hold_start, "finish": hold_end}],
            "provenance": {"real": "DataCo Smart Supply Chain (doi:10.17632/8gx2fvg2k6.5, CC BY 4.0), sha256 "
                                   "fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6",
                           "notional": f"BOM, stock, POs, capacity, supplier disruption, quality hold (fixed rules, seed {SEED})"}}


def mrpspeed_fixture() -> dict:
    """The committed derived dataset (rebuild with scripts/build_mrpspeed_demo.py)."""
    return json.loads(DATASET.read_text(encoding="utf-8"))
