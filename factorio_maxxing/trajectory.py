"""Trajectory recording.

See docs/contracts.md (trajectory.py) and docs/decisions.md D6, D9 and D22.

The recorder is passive. It writes what it is handed and returns nothing: it never
determines success, never inspects a verdict to decide anything, and never influences
control flow. Its output exists so that a verifier's claim can later be checked against
objective state.

Four record types share one discriminator, `type`. All model usage - policy, verifier,
and every future caller - is an `llm_call` record carrying a `role`, so cost analysis
stays `sum(r["input_tokens"] for r in records if r["type"] == "llm_call")` however many
callers the harness grows (D22).
"""

import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from factorio_maxxing.goal import Goal
from factorio_maxxing.llm import LLMResponse
from factorio_maxxing.verifier import VerificationResult


class TrajectoryRecorder:
    """Appends JSONL records for one run.

    Each record is flushed as it is written, so a run that dies mid-goal keeps
    everything recorded up to that point.
    """

    def __init__(self, path: Path | str, run_id: str | None = None):
        self.path = Path(path)
        self.run_id = run_id or uuid.uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8", newline="\n")

    def record_step(
        self,
        step: int,
        goal: Goal,
        policy: str,
        observation: dict[str, Any],
        reward: float,
        execution_errors: Sequence[str] = (),
    ) -> None:
        """Record one environment step.

        ``policy`` may be the empty string: a response carrying no usable code is
        legitimate recorded data, not a missing field (D17).
        """
        self._write(
            "step",
            step,
            goal=goal.description,
            policy=policy,
            observation=observation,
            reward=reward,
            execution_errors=list(execution_errors),
        )

    def record_llm_call(self, step: int, role: str, response: LLMResponse) -> None:
        """Record raw usage for one model call. Never a computed cost (D9)."""
        self._write(
            "llm_call",
            step,
            role=role,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            latency_seconds=response.latency_seconds,
        )

    def record_verification(self, step: int, result: VerificationResult) -> None:
        """Record a verdict as control-flow evidence. Skipped steps record nothing."""
        self._write("verification", step, done=result.done, reason=result.reason)

    def record_intervention(
        self,
        step: int,
        stuck_reason: str,
        text: str,
        intervention_index: int,
        original_step: int | None = None,
    ) -> None:
        """Record one intervention.

        ``text`` is written verbatim and is the source of truth. Any later
        classification is an additional field; it never replaces this text.
        """
        self._write(
            "intervention",
            step,
            stuck_reason=stuck_reason,
            text=text,
            intervention_index=intervention_index,
            original_step=original_step,
        )

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "TrajectoryRecorder":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _write(self, record_type: str, step: int, **fields: Any) -> None:
        record = {"type": record_type, "run_id": self.run_id, "step": step, **fields}
        # default=str keeps an unexpected value in an observation from killing a run
        # that has already spent real API budget.
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()


def read_trajectory(path: Path | str) -> list[dict[str, Any]]:
    """Read a trajectory back, in write order. Blank lines are ignored."""
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
