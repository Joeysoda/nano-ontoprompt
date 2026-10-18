import pytest

from app.services.v2.solver_spike import _status_from_milp, capabilities, solve_assignment


def test_highs_spike_matches_known_assignment_and_infeasible_fixtures():
    if not capabilities()["highs_via_scipy_milp"]["available"]:
        pytest.skip("Optional SciPy/HiGHS spike dependency is not installed")

    optimum = solve_assignment([[5, 2], [4, 6]], time_limit_seconds=5)
    assert optimum.status == "optimal"
    assert optimum.objective == pytest.approx(6)
    assert optimum.assignments == [{"task": 0, "resource": 1}, {"task": 1, "resource": 0}]
    assert optimum.best_bound == pytest.approx(6)
    assert optimum.gap == pytest.approx(0)
    assert optimum.solver_version
    assert optimum.time_limit_seconds == 5

    impossible = solve_assignment(
        [[1, 2], [3, 4]],
        allowed=[[True, False], [True, False]],
    )
    assert impossible.status == "infeasible"
    assert impossible.objective is None


def test_solver_spike_reports_missing_ortools_and_validates_limits():
    caps = capabilities()
    assert caps["ortools_cp_sat"]["available"] is False
    assert caps["ortools_cp_sat"]["status"] == "capability_unavailable"
    with pytest.raises(ValueError, match="positive"):
        solve_assignment([[1]], time_limit_seconds=0)
    assert _status_from_milp(1, has_incumbent=False) == "timeout"
    assert _status_from_milp(1, has_incumbent=True) == "feasible"
