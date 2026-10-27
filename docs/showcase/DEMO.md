# Material-shortage walkthrough

This is a synthetic planning exercise with a local fake procurement connector. It requires no external model call. Both generated accounts are local demo administrators so the exercise can focus on second-person review; they are not a least-privilege role deployment.

## Start

From the repository root in Bash:

```bash
bash scripts/bootstrap_deployment.sh --seed-decision-demo
```

Requires Docker Compose 2.24.4+ because `docker-compose.local.yml` uses `!override`. The script reuses existing service secrets and volumes, persists JWT mode for backend/frontend, and creates a fresh demo with new accounts. It does not clear existing ontologies. This is a local development stack; service ports in the local overlay bind to loopback.

The seeder waits up to 240 seconds for a completed study and verifies that baseline risk is 550,000 and the combined response leaves zero risk at an incremental cost of 3,720. A timeout or a mismatching result fails setup. Credentials and the direct URL are written to `.decision-demo.json` only after the seeder succeeds. Open that file with a local editor; do not upload it or include it in logs.

The service stack is still the full Compose topology, not a lightweight demo profile. There is no claimed clean-machine startup time or memory figure yet. A successful seed checks the baseline; it does not itself certify browser behavior or the subsequent apply/revert operations.

## Walk through the decision

1. At `http://127.0.0.1:15173`, sign in using the file's `accounts.author`. Then open its `url`; login currently returns to the overview, so open the study link after login.
2. Inspect baseline revenue at risk, late/blocked orders and the component path to CHIP-X.
3. Compare the expedite, qualified alternate, stock reallocation and combined responses. Stock reallocation should visibly harm other orders; the unqualified supplier should be inapplicable.
4. Select the combined response. Inspect the changes to supply availability, purchase quantity and production-line reassignment. Supply a rationale and create a Decision Package.
5. Submit the package. Trying to approve it as the author must fail with `self_review_forbidden`.
6. Sign out using the sidebar. Sign in using `accounts.reviewer`, reopen the same study URL and approve the package. Use a private browser window if you want both sessions visible simultaneously.
7. Apply the approved package. Inspect its edits, audit events and fake ERP receipt. Reload the page to inspect persisted state.
8. Revert the object changes. Inspect the effects that cannot be automatically undone; the fake connector's notification receipt is not evidence of an actual purchase.

No command-line approval or direct database change is needed during this walkthrough. The old `review_package_as_second_user.py` script remains an acceptance helper for the older single-user environment.

## Inspect services

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
docker compose -f docker-compose.yml -f docker-compose.local.yml logs --tail=80 backend celery_worker
```

Backend health: `http://127.0.0.1:18080/health`; API docs: `http://127.0.0.1:18080/docs`. Never share `.env`, `.decision-demo.json` or logs containing personal data. Do not remove data volumes to recover from a failed seed. A failed setup may leave its newly created demo objects; another run creates a separate demo.

## Optional model commentary

Configure `OPENAI_API_KEY` locally only if you want to use the explanation feature. The calculation, hard constraints and approval rules are deterministic and work without it. A model outage should appear as an explanation error, not a fabricated fallback explanation.

Explanation text is semantically unverified. The API reports the limited scope of citation-existence and numeric-occurrence checks separately. The summary is unchecked, and identical numbers can still refer to different metrics or entities. This feature cannot approve or apply a package.

## Evidence

The [known-answer tests](../../backend/unit_tests/test_l3harris_known_answer.py) check expected allocations and response outcomes. The [real-service acceptance](../../backend/tests/v2/test_material_decision_apply_real_falkor.py) checks actual application and reversion. The [verification record](VERIFICATION.md) says which checks were executed in the current environment. No new browser recording or deployment success is claimed merely because these steps are documented.
