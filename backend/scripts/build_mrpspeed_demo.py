"""Rebuild data/mrpspeed_demo/mrpspeed_demo.json from the DataCo CSV.

    python -I scripts/build_mrpspeed_demo.py <path-to>/DataCoSupplyChainDataset.csv
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.v2.mrpspeed_fixture import DATASET, build  # noqa: E402

EXPECTED = "fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6"

if __name__ == "__main__":
    source = Path(sys.argv[1])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != EXPECTED:
        raise SystemExit(f"unexpected source checksum {digest}")
    data = build(source)
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print({key: len(value) for key, value in data.items() if isinstance(value, list)})
