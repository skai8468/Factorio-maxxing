"""Tests for stuck detection.

Required coverage (build-plan section 21, Stuck detector): fires at threshold, does
not fire during legitimate incomplete progress, repeated error detection.
"""

import pytest

from factorio_maxxing.stuck import (
    CompositeStuckDetector,
    ConsecutiveNonDoneDetector,
    RepeatedErrorDetector,
    StuckDetector,
    default_detector,
    error_signature,
)
from factorio_maxxing.verifier import VerificationResult

NOT_DONE = VerificationResult(done=False, reason="still building")
DONE = VerificationResult(done=True, reason="goal met")
HISTORY = [("x = 1", "obs")]
NO_ERRORS = ["", "", ""]


def check(detector, verifications=(), errors=(), history=HISTORY, flows=()):
    return detector.is_stuck(history, list(verifications), list(flows), list(errors))


def test_detectors_satisfy_the_protocol():
    assert isinstance(ConsecutiveNonDoneDetector(), StuckDetector)
    assert isinstance(RepeatedErrorDetector(), StuckDetector)
    assert isinstance(default_detector(), StuckDetector)


def test_consecutive_detector_fires_at_threshold():
    stuck, reason = check(ConsecutiveNonDoneDetector(3), [NOT_DONE] * 3)
    assert stuck is True
    assert "3 consecutive non-DONE verifications" == reason


def test_consecutive_detector_does_not_fire_below_threshold():
    """Legitimate incomplete progress: a build under way is not a stuck agent."""
    assert check(ConsecutiveNonDoneDetector(3), [NOT_DONE] * 2) == (False, "")


def test_consecutive_detector_does_not_fire_with_no_verifications_yet():
    assert check(ConsecutiveNonDoneDetector(3), []) == (False, "")


def test_a_done_verification_breaks_the_streak():
    verifications = [NOT_DONE, NOT_DONE, DONE, NOT_DONE, NOT_DONE]
    assert check(ConsecutiveNonDoneDetector(3), verifications) == (False, "")


def test_consecutive_detector_counts_verification_events_not_steps():
    """Sparse verification: twelve steps at interval 4 produce three events."""
    detector = ConsecutiveNonDoneDetector(3)
    assert check(detector, [NOT_DONE] * 2, errors=[""] * 12) == (False, "")
    assert check(detector, [NOT_DONE] * 3, errors=[""] * 12)[0] is True


def test_consecutive_detector_rejects_a_threshold_below_one():
    with pytest.raises(ValueError, match="threshold must be >= 1"):
        ConsecutiveNonDoneDetector(0)


def test_error_signature_uses_the_last_traceback_line():
    traceback = "Traceback (most recent call last):\n  File x\nNameError: place_entity"
    assert error_signature(traceback) == "nameerror: place_entity"


def test_error_signature_normalises_case_and_spacing():
    assert error_signature("NameError:   place_entity") == error_signature(
        "nameerror: place_entity"
    )


def test_error_signature_of_nothing_is_empty():
    assert error_signature("") == ""
    assert error_signature(None) == ""


def test_repeated_error_detector_fires_on_the_same_error():
    stuck, reason = check(RepeatedErrorDetector(3), errors=["NameError: x"] * 3)
    assert stuck is True
    assert "3 repeated execution errors" in reason
    assert "nameerror: x" in reason


def test_repeated_error_detector_ignores_differing_errors():
    errors = ["NameError: x", "TypeError: y", "ValueError: z"]
    assert check(RepeatedErrorDetector(3), errors=errors) == (False, "")


def test_a_clean_step_breaks_the_error_streak():
    """Fail, recover, fail is not a loop; three identical failures running is."""
    errors = ["NameError: x", "", "NameError: x"]
    assert check(RepeatedErrorDetector(3), errors=errors) == (False, "")


def test_repeated_error_detector_does_not_fire_below_threshold():
    assert check(RepeatedErrorDetector(3), errors=["NameError: x"] * 2) == (False, "")


def test_repeated_error_detector_ignores_clean_runs():
    assert check(RepeatedErrorDetector(3), errors=NO_ERRORS) == (False, "")


def test_repeated_error_detector_looks_only_at_the_most_recent_window():
    errors = ["NameError: x"] * 3 + ["", "", ""]
    assert check(RepeatedErrorDetector(3), errors=errors) == (False, "")


def test_composite_fires_when_any_detector_fires():
    detector = default_detector(3)
    assert check(detector, errors=["NameError: x"] * 3)[0] is True
    assert check(detector, [NOT_DONE] * 3, errors=NO_ERRORS)[0] is True


def test_composite_stays_quiet_during_a_healthy_build():
    """Two non-DONE verifications and clean execution: do not interrupt."""
    assert check(default_detector(3), [NOT_DONE] * 2, errors=NO_ERRORS) == (False, "")


def test_composite_reports_the_first_firing_detector():
    detector = default_detector(3)
    _, reason = check(detector, [NOT_DONE] * 3, errors=["NameError: x"] * 3)
    assert "repeated execution errors" in reason


def test_error_signature_is_the_fast_path_when_verification_is_sparse():
    """D7: consecutive non-DONE alone would not fire for twelve steps."""
    detector = default_detector(3)
    stuck, reason = check(detector, [NOT_DONE], errors=["NameError: x"] * 3)
    assert stuck is True
    assert "repeated execution errors" in reason


def test_composite_requires_at_least_one_detector():
    with pytest.raises(ValueError, match="at least one detector"):
        CompositeStuckDetector([])


def test_detectors_do_not_mutate_their_inputs():
    verifications = [NOT_DONE] * 3
    errors = ["NameError: x"] * 3
    default_detector(3).is_stuck(HISTORY, verifications, [], errors)
    assert verifications == [NOT_DONE] * 3
    assert errors == ["NameError: x"] * 3
