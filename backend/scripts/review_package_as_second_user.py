"""Approve or reject a Decision Package as a second, separate reviewer account.

The local workbench runs in single-user mode, where every browser request is
the same admin, and that admin is correctly forbidden from reviewing their own
package.  This script stands in for "another person" during local acceptance
runs: it uses a dedicated ``demo-reviewer`` account and the same transition
code as the API.  It is a simulation aid, not a production review path.

    python scripts/review_package_as_second_user.py <ontology_id> <package_id> approve
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.v2.decisions import PackageTransitionInput, transition_package  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402


def main():
    ontology_id, package_id, decision = sys.argv[1], sys.argv[2], sys.argv[3]
    reason = sys.argv[4] if len(sys.argv) > 4 else ""
    with SessionLocal() as db:
        reviewer = db.query(User).filter_by(username="demo-reviewer").first()
        if not reviewer:
            reviewer = User(id=str(uuid.uuid4()), username="demo-reviewer", email="demo-reviewer@local.invalid",
                            password_hash=hash_password(uuid.uuid4().hex), role="admin")
            db.add(reviewer)
            db.commit()
        from app.models.v2.decision import DecisionPackage
        package = db.get(DecisionPackage, package_id)
        result = transition_package(ontology_id, package_id, PackageTransitionInput(
            event=decision, expected_etag=package.etag, request_id=f"second-reviewer:{uuid.uuid4().hex}", reason=reason), db=db, user=reviewer)
        print(f"{result['status']} by {reviewer.username}")


if __name__ == "__main__":
    main()
