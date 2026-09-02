"""Stuck detection.

See docs/contracts.md (stuck.py) and docs/decisions.md D7 and D12.

A detector decides one thing: should the harness ask a human for help? It never decides
goal completion, never modifies state, and never replaces the verifier.

Detectors are pure functions of the sequences they are given. They hold no counters, so
the intervention reset in D12 is the loop's job: after an intervention the loop passes
only the events recorded since that intervention. Keeping the state in one place stops
the loop and the detector from disagreeing about when the window began.

The two detectors here run on different clocks. Consecutive non-DONE counts
verification *events*, which are sparse when verification_interval > 1; the error
signature detector counts steps, and is the fast path when verification is sparse.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from factorio_maxxing.verifier import VerificationResult

STUCK_THRESHOLD = 3

History = Sequence[tuple[str, str]]
Verifications = Sequence[VerificationResult]
Flows = Sequence[Any]
Errors = Sequence[str | None]

NOT_STUCK: tuple[bool, str] = (False, "")


@runtime_checkable
class StuckDetector(Protocol):
    def is_stuck(
        self,
        history: History,
        verifications: Verifications,
        flows: Flows,
        errors: Errors,
    ) -> tuple[bool, str]: ...


def error_signature(error: str | None) -> str:
    """Reduce an execution error to a comparable signature.

    The last non-empty line of a traceback carries the exception type and message,
    which is what makes two failures 'the same failure'. Case and spacing are
    normalised; nothing else is stripped, so two genuinely different errors stay
    different.
    """
    if not error:
        return ""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return " ".join(lines[-1].split()).lower() if lines else ""


class ConsecutiveNonDoneDetector:
    """Fires after `threshold` consecutive non-DONE verification events.

    Counts verification events, not steps: at verification_interval 4 with threshold 3
    this cannot fire for twelve steps, which is why it is never used alone (D7).
    """

    def __init__(self, threshold: int = STUCK_THRESHOLD):
        if threshold < 1:
            raise ValueError(f"threshold must be >= 1, got {threshold}")
        self.threshold = threshold

    def is_stuck(
        self,
        history: History,
        verifications: Verifications,
        flows: Flows,
        errors: Errors,
    ) -> tuple[bool, str]:
        streak = 0
        for result in reversed(list(verifications)):
            if result.done:
                break
            streak += 1

        if streak >= self.threshold:
            return True, f"{streak} consecutive non-DONE verifications"
        return NOT_STUCK


class RepeatedErrorDetector:
    """Fires when the last `threshold` steps failed with the same error signature.

    `errors` carries one entry per step, empty where the step ran cleanly, so a
    successful step breaks the streak. An agent that fails, recovers, then fails
    differently is not stuck in a loop; an agent hitting the same exception three times
    running is.
    """

    def __init__(self, threshold: int = STUCK_THRESHOLD):
        if threshold < 1:
            raise ValueError(f"threshold must be >= 1, got {threshold}")
        self.threshold = threshold

    def is_stuck(
        self,
        history: History,
        verifications: Verifications,
        flows: Flows,
        errors: Errors,
    ) -> tuple[bool, str]:
        recent = [error_signature(error) for error in list(errors)[-self.threshold :]]
        if len(recent) < self.threshold or not recent[0]:
            return NOT_STUCK
        if all(signature == recent[0] for signature in recent):
            return True, f"{self.threshold} repeated execution errors: {recent[0]}"
        return NOT_STUCK


class CompositeStuckDetector:
    """Runs several detectors and reports the first that fires.

    Composition is what keeps consecutive non-DONE from being the sole signal (D7).
    Order is the order given, so the cheapest or most specific detector can be first.
    """

    def __init__(self, detectors: Sequence[StuckDetector]):
        if not detectors:
            raise ValueError("CompositeStuckDetector requires at least one detector")
        self.detectors = tuple(detectors)

    def is_stuck(
        self,
        history: History,
        verifications: Verifications,
        flows: Flows,
        errors: Errors,
    ) -> tuple[bool, str]:
        for detector in self.detectors:
            stuck, reason = detector.is_stuck(history, verifications, flows, errors)
            if stuck:
                return True, reason
        return NOT_STUCK


def default_detector(threshold: int = STUCK_THRESHOLD) -> CompositeStuckDetector:
    """The `consecutive_failures+error_signature` pairing named in the config."""
    return CompositeStuckDetector(
        [RepeatedErrorDetector(threshold), ConsecutiveNonDoneDetector(threshold)]
    )
