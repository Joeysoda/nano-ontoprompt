"""Reviewed synthetic fixture matching the complexity of the MRPSpeed demo.

Synthetic data, not a real customer dataset.  Every number is chosen so the
expected outcome can be recomputed by hand (see
unit_tests/test_l3harris_known_answer.py).  Times are day offsets from scenario_time.

Story:
* CHIP-X is shared by all three products (2 per unit) and is the bottleneck.
* The primary supplier PO for CHIP-X (PO-LATE) slips to day 9, after every due
  date; it can be expedited to day 4 for a premium.
* PSU-A looks short on hand (2 vs 6 needed) but an in-transit PO arrives on day
  2, in time: an apparent shortage that is not a real one.
* A 100-unit CHIP-X commitment has expired and must not count.
* Supplier-B is qualified but expensive; Supplier-C is cheap but unqualified.
* assembly-2 is blocked by a fixed rework task on days 1-4.
"""

ORDERS = [
    # id, product, qty, due day, priority, primary resource
    ("ORD-1001", "RADAR-A", 1, 5, 9, "assembly-1"),
    ("ORD-1002", "RADAR-B", 2, 5, 8, "assembly-2"),
    ("ORD-1003", "COMMS-C", 1, 5, 7, "assembly-1"),
    ("ORD-1004", "RADAR-A", 2, 6, 9, "assembly-2"),
    ("ORD-1005", "RADAR-B", 1, 6, 6, "assembly-1"),
    ("ORD-1006", "COMMS-C", 2, 6, 5, "assembly-2"),
    ("ORD-1007", "RADAR-A", 1, 7, 9, "assembly-1"),
    ("ORD-1008", "RADAR-B", 1, 7, 4, "assembly-2"),
    ("ORD-1009", "COMMS-C", 1, 7, 3, "assembly-1"),
    ("ORD-1010", "RADAR-A", 2, 8, 9, "assembly-2"),
    ("ORD-1011", "RADAR-B", 1, 8, 2, "assembly-1"),
    ("ORD-1012", "COMMS-C", 2, 8, 1, "assembly-2"),
]
UNIT_REVENUE = {"RADAR-A": 125000, "RADAR-B": 90000, "COMMS-C": 70000}
LINES = ("assembly-1", "assembly-2")


def l3harris_material_shortage_fixture() -> dict:
    demands = [{"id": order_id, "item": product, "quantity": quantity, "due_at": due, "priority": priority,
                "unit_revenue": UNIT_REVENUE[product], "processing_days": 1, "resource": resource,
                "alternate_resources": [line for line in LINES if line != resource]}
               for order_id, product, quantity, due, priority, resource in ORDERS]
    boms = []
    for product in UNIT_REVENUE:
        suffix = product[-1]
        boms.extend({"parent_item": product, "component_item": f"{kind}-{suffix}", "quantity_per": 1} for kind in ("RF", "POWER", "FRAME"))
        boms.extend([
            {"parent_item": f"RF-{suffix}", "component_item": "CHIP-X", "quantity_per": 2},
            {"parent_item": f"RF-{suffix}", "component_item": f"PCB-{suffix}", "quantity_per": 1},
            {"parent_item": f"RF-{suffix}", "component_item": f"FILTER-{suffix}", "quantity_per": 1},
            {"parent_item": f"POWER-{suffix}", "component_item": f"PSU-{suffix}", "quantity_per": 1},
            {"parent_item": f"POWER-{suffix}", "component_item": f"HARNESS-{suffix}", "quantity_per": 2},
            {"parent_item": f"FRAME-{suffix}", "component_item": f"CASTING-{suffix}", "quantity_per": 1},
            {"parent_item": f"FRAME-{suffix}", "component_item": f"FASTENER-{suffix}", "quantity_per": 8},
        ])
    inventory = [{"id": "LOT-CHIP-MAIN", "item": "CHIP-X", "location": "MAIN", "quantity": 16},
                 {"id": "LOT-CHIP-REPAIR", "item": "CHIP-X", "location": "REPAIR", "quantity": 2},
                 {"id": "LOT-PSU-A", "item": "PSU-A", "location": "EAST", "quantity": 2}]
    leaf_items = sorted({row["component_item"] for row in boms if row["component_item"] not in {"CHIP-X", "PSU-A"}
                         and not row["component_item"].startswith(("RF-", "POWER-", "FRAME-"))})
    inventory.extend({"id": f"LOT-{item}", "item": item, "location": ["MAIN", "EAST", "WEST"][index % 3], "quantity": 100}
                     for index, item in enumerate(leaf_items))
    return {
        "scenario_time": "2026-01-01T00:00:00Z", "demands": demands, "boms": boms, "inventory": inventory,
        "reservations": [{"id": "RSV-1002-CHIP", "item": "CHIP-X", "demand_id": "ORD-1002", "quantity": 4, "status": "active"}],
        "supply_commitments": [
            {"id": "PO-IN-TIME", "supplier": "Supplier-A", "item": "CHIP-X", "quantity": 4, "available_at": 3, "status": "active", "unit_cost": 80},
            {"id": "PO-LATE", "supplier": "Supplier-A", "item": "CHIP-X", "quantity": 8, "available_at": 9, "status": "active", "unit_cost": 80,
             "expedite_available_at": 4, "expedite_unit_premium": 400},
            {"id": "PO-EXPIRED", "supplier": "Supplier-A", "item": "CHIP-X", "quantity": 100, "available_at": 0,
             "expires_at": "2025-12-31T00:00:00Z", "status": "active", "unit_cost": 70},
            {"id": "PO-PSU-A", "supplier": "Supplier-P", "item": "PSU-A", "quantity": 5, "available_at": 2, "status": "active", "unit_cost": 300},
        ],
        "supply_options": [
            {"id": "ALT-QUALIFIED", "supplier": "Supplier-B", "item": "CHIP-X", "quantity": 4, "lead_time": 3, "unit_cost": 130, "qualified": True},
            {"id": "ALT-UNQUALIFIED", "supplier": "Supplier-C", "item": "CHIP-X", "quantity": 100, "lead_time": 1, "unit_cost": 55, "qualified": False},
        ],
        "fixed_tasks": [{"id": "FIXED-L2-REWORK", "resource": "assembly-2", "start": 1, "finish": 4}],
    }
