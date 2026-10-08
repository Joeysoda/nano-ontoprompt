"""Export the generated Object Query JSON Schema deterministically."""
from __future__ import annotations

import json
from pathlib import Path

from app.schemas.v2.object_query import object_query_json_schema


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "app" / "schemas" / "v2" / "object_query.schema.json"
    target.write_text(
        json.dumps(object_query_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
