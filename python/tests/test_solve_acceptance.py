import pytest

from PiFinder.solve_acceptance import (
    SolveContinuityGate,
    angular_separation_deg,
    solution_quality_decision,
)


pytestmark = pytest.mark.unit


def _solution(ra=306.0, dec=-20.5, matches=7, rmse=100.0, prob=1e-6):
    return {
        "RA": ra,
        "Dec": dec,
        "Matches": matches,
        "RMSE": rmse,
        "Prob": prob,
    }


def test_quality_rejects_observed_marginal_six_match_sep_solution():
    decision = solution_quality_decision(
        _solution(matches=6, rmse=80.5, prob=9.542e-5), "sep_center"
    )
    assert decision.accepted is False
    assert decision.reason == "matches_below_7"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"RMSE": 234.0}, "rmse_too_high"),
        ({"Prob": 8e-5}, "false_probability_too_high"),
        ({"RMSE": None}, "missing_quality_metrics"),
    ],
)
def test_quality_rejects_bad_fullframe_metrics(changes, reason):
    solution = _solution()
    solution.update(changes)
    decision = solution_quality_decision(solution, "sep_full")
    assert decision.accepted is False
    assert decision.reason == reason


def test_quality_keeps_observed_stable_sep_and_established_paths():
    assert solution_quality_decision(
        _solution(matches=7, rmse=96.8, prob=1.685e-6), "sep_center"
    ).accepted
    # The quality policy does not alter the established production crop.
    assert solution_quality_decision({"RA": 1.0}, "cedar_512").accepted


def test_quality_gates_native_cedar_center_path():
    decision = solution_quality_decision(
        _solution(matches=5, rmse=90.0, prob=1e-6), "cedar_center"
    )
    assert decision.accepted is False
    assert decision.reason == "matches_below_6"


def test_angular_separation_handles_ra_wrap():
    assert angular_separation_deg(359.5, 0.0, 0.5, 0.0) == pytest.approx(1.0)


def test_initial_fullframe_solution_requires_two_agreeing_frames():
    gate = SolveContinuityGate()
    first = gate.evaluate(_solution(), "sep_center", 100.0)
    second = gate.evaluate(_solution(ra=306.2, dec=-20.4), "sep_center", 102.0)
    assert first.accepted is False
    assert first.reason == "initial_fullframe_confirmation"
    assert second.accepted is True
    assert second.reason == "confirmed_jump"


def test_established_crop_can_seed_anchor_immediately():
    gate = SolveContinuityGate()
    decision = gate.evaluate(_solution(), "cedar_512", 100.0)
    assert decision.accepted is True
    assert decision.reason == "initial_established_anchor"


def test_near_trusted_is_immediate_but_large_jump_needs_confirmation():
    gate = SolveContinuityGate()
    assert gate.evaluate(_solution(), "cedar_512", 100.0).accepted
    assert gate.evaluate(_solution(ra=307.0), "sep_center", 102.0).accepted

    jump = gate.evaluate(_solution(ra=120.0, dec=30.0), "sep_center", 104.0)
    assert jump.accepted is False
    assert jump.reason == "jump_confirmation"

    confirmed = gate.evaluate(_solution(ra=120.4, dec=30.2), "sep_center", 106.0)
    assert confirmed.accepted is True
    assert confirmed.reason == "confirmed_jump"


def test_expired_or_disagreeing_pending_solution_does_not_confirm():
    gate = SolveContinuityGate(confirm_max_age_s=5.0)
    assert not gate.evaluate(_solution(), "sep_center", 100.0).accepted
    expired = gate.evaluate(_solution(ra=306.1), "sep_center", 110.0)
    assert expired.accepted is False
    disagree = gate.evaluate(_solution(ra=200.0, dec=40.0), "sep_center", 111.0)
    assert disagree.accepted is False


def test_return_to_trusted_clears_a_false_jump_candidate():
    gate = SolveContinuityGate()
    assert gate.evaluate(_solution(), "cedar_512", 100.0).accepted
    assert not gate.evaluate(_solution(ra=100.0, dec=20.0), "sep_full", 101.0).accepted
    back = gate.evaluate(_solution(ra=306.1), "sep_center", 102.0)
    assert back.accepted is True
    # A later different jump must start confirmation from scratch.
    assert not gate.evaluate(_solution(ra=100.2, dec=20.1), "sep_full", 103.0).accepted
