import pytest

from PiFinder.preprocess_bias import PreprocessBiasTracker
from PiFinder.solve_acceptance import SolveContinuityGate
from PiFinder.solver_scheduling import SolverSchedulingPolicy

pytestmark = pytest.mark.unit


def test_auto_qualifies_raw_before_switching_to_background():
    policy = SolverSchedulingPolicy()
    assert [policy.choose(raw_solved=True) for _ in range(5)] == [
        "sync",
        "sync",
        "async",
        "async",
        "async",
    ]


def test_failed_raw_is_recovered_synchronously_on_that_frame():
    policy = SolverSchedulingPolicy()
    for _ in range(5):
        policy.choose(raw_solved=True)
        policy.record_publication(accepted=True)
    assert policy.execution == "async"

    assert policy.choose(raw_solved=False) == "sync"
    assert policy.reason == "raw_failed_same_frame_recovery"
    # Successful preprocessed solves must not be mistaken for successful RAW.
    for _ in range(10):
        policy.record_publication(accepted=True)
        assert policy.choose(raw_solved=False) == "sync"


def test_intermittent_raw_does_not_cause_mode_flapping():
    policy = SolverSchedulingPolicy()
    for success in [True, True, False] * 5:
        assert policy.choose(raw_solved=success) == "sync"
        policy.record_publication(accepted=True)
    assert [policy.choose(raw_solved=True) for _ in range(3)] == [
        "sync",
        "sync",
        "async",
    ]


def test_one_normal_continuity_hold_keeps_fast_mode():
    policy = SolverSchedulingPolicy()
    for _ in range(3):
        policy.choose(raw_solved=True)
    policy.record_publication(accepted=False)
    assert policy.choose(raw_solved=True) == "async"
    policy.record_publication(accepted=True)
    assert policy.raw_rejection_streak == 0


def test_repeated_holds_recover_even_when_raw_reports_patterns():
    policy = SolverSchedulingPolicy()
    for _ in range(3):
        policy.choose(raw_solved=True)
    for _ in range(2):
        policy.choose(raw_solved=True)
        policy.record_publication(accepted=False)
    assert policy.reason == "raw_publication_stalled"
    assert policy.choose(raw_solved=True) == "sync"
    assert policy.raw_success_streak == 1


@pytest.mark.parametrize("reason", ("moving", "optics_changed", "target_pixel_changed"))
def test_new_frame_context_requalifies_fast_mode(reason):
    policy = SolverSchedulingPolicy()
    for _ in range(3):
        policy.choose(raw_solved=True)
    policy.reset(reason)
    assert policy.reason == reason
    assert policy.choose(raw_solved=True) == "sync"


def test_alignment_and_calibration_always_use_same_frame():
    policy = SolverSchedulingPolicy()
    for _ in range(3):
        policy.choose(raw_solved=True)
    for _ in range(5):
        assert policy.choose(raw_solved=True, forced_sync=True) == "sync"
    assert policy.choose(raw_solved=True) == "sync"


def test_explicit_sync_mode_never_switches():
    policy = SolverSchedulingPolicy("sync")
    for _ in range(10):
        assert policy.choose(raw_solved=True) == "sync"


def test_invalid_mode_is_not_silently_enabled():
    with pytest.raises(ValueError):
        SolverSchedulingPolicy("invalid")


def test_bias_and_continuity_survive_a_normal_sync_to_async_handoff():
    policy = SolverSchedulingPolicy()
    bias = PreprocessBiasTracker(required_samples=2)
    gate = SolveContinuityGate()
    raw = {"RA": 10.0, "Dec": 20.0}
    preprocessed = {"RA": 10.05, "Dec": 19.98}
    accepted = []
    executions = []
    for frame in range(1, 7):
        execution = policy.choose(raw_solved=True)
        executions.append(execution)
        if execution == "sync":
            assert bias.update(raw, preprocessed)
            solution, path = preprocessed, "preprocessed_cedar_center"
        else:
            assert bias.ready
            solution, path = bias.apply(raw), "cedar_512"
        decision = gate.evaluate(
            solution, path, float(frame), stationary=True, prefer_preprocessed=True
        )
        accepted.append(decision.accepted)
        policy.record_publication(accepted=decision.accepted)

    # Cold-start and RAW handover each need confirmation, which must not
    # trigger an endless return to synchronous preprocessing.
    assert executions == ["sync", "sync", "async", "async", "async", "async"]
    assert accepted == [False, True, False, True, True, True]


def test_repeated_inconsistent_raw_coordinates_return_to_recovery():
    policy = SolverSchedulingPolicy()
    gate = SolveContinuityGate()
    preferred = {"RA": 10.0, "Dec": 20.0}
    for frame in (1, 2):
        policy.choose(raw_solved=True)
        gate.evaluate(
            preferred,
            "preprocessed_cedar_center",
            float(frame),
            stationary=True,
            prefer_preprocessed=True,
        )
    for frame, ra in ((3, 15.0), (4, 25.0)):
        assert policy.choose(raw_solved=True) == "async"
        decision = gate.evaluate(
            {"RA": ra, "Dec": 20.0},
            "cedar_512",
            float(frame),
            stationary=True,
            prefer_preprocessed=True,
        )
        assert not decision.accepted
        policy.record_publication(accepted=decision.accepted)
    assert policy.choose(raw_solved=True) == "sync"
