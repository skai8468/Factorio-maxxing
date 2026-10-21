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
from collections.abc import Mapping, Sequence
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

    def record_run(
        self, goal: Goal, config: Mapping[str, Any], code: Mapping[str, Any]
    ) -> None:
        """Record what the run was: its goal, resolved config and code version (D70).

        Written once, first, so a trajectory says on its own which grid cell it is,
        which models and human backend it used, and which commit produced it. It has
        no step. The config holds no secrets - API keys never enter it (D27) - and no
        dollar figure beyond the configured ceiling.
        """
        self._write(
            "run",
            None,
            goal=goal.description,
            open_ended=goal.open_ended,
            max_steps=goal.max_steps,
            config=dict(config),
            code=dict(code),
        )

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
        """Record raw usage for one model call. Never a computed cost (D9).

        ``key_id`` - which API key paid, as a fingerprint, never the key - is written
        when the client used one, so spend can be totalled per key (D56).
        """
        extra = {"key_id": response.key_id} if response.key_id else {}
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
            **extra,
        )

    def record_verification(self, step: int, result: VerificationResult) -> None:
        """Record a verdict as control-flow evidence. Skipped steps record nothing."""
        self._write("verification", step, done=result.done, reason=result.reason)

    def record_help_request(self, step: int, stuck_reason: str) -> None:
        """Record that help was requested, before anyone answers (D72).

        The matching ``intervention`` record follows once the human answers or
        declines; until then this is how a reader - the watcher's panel - knows the run
        is waiting, and a run that dies while waiting still shows it was asking.
        """
        self._write("help_request", step, stuck_reason=stuck_reason)

    def record_intervention(
        self,
        step: int,
        stuck_reason: str,
        text: str,
        intervention_index: int,
        original_step: int | None = None,
        answer_seconds: float | None = None,
    ) -> None:
        """Record one intervention.

        ``text`` is written verbatim and is the source of truth. Any later
        classification is an additional field; it never replaces this text.
        ``answer_seconds`` is the wall-clock time the human took (build-plan 5c, D72).
        """
        self._write(
            "intervention",
            step,
            stuck_reason=stuck_reason,
            text=text,
            intervention_index=intervention_index,
            original_step=original_step,
            answer_seconds=answer_seconds,
        )

    def record_report(self, step: int, text: str) -> None:
        """Record a new history report verbatim (D66). It covers steps 0 to ``step``;
        written so an annotator can see what the agent was told it had done."""
        self._write("report", step, text=text)

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "TrajectoryRecorder":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _write(self, record_type: str, step: int | None, **fields: Any) -> None:
        record = {"type": record_type, "run_id": self.run_id, "step": step, **fields}
        # default=str keeps an unexpected value in an observation from killing a run
        # that has already spent real API budget.
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()


def read_trajectory(path: Path | str) -> list[dict[str, Any]]:
    """Read a trajectory back, in write order. Blank lines are ignored."""
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def extract_map_images(path: Path | str, out_dir: Path | str) -> list[Path]:
    """Write each step's rendered map image out as a PNG, in step order.

    Reading is the counterpart of recording, so it lives here (D38). The images are
    only present when a run was made with ``enable_vision``; a run without it yields
    an empty list rather than an error, since that is the normal case.

    Files are named by step so they sort correctly and so a gap is visible: a step
    whose render failed is skipped, not renumbered over.
    """
    import base64
    import binascii

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    written = []
    for record in read_trajectory(path):
        if record.get("type") != "step":
            continue
        encoded = (record.get("observation") or {}).get("map_image")
        if not encoded:
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not data:
            continue
        target = directory / f"step-{record['step']:04d}.png"
        target.write_bytes(data)
        written.append(target)
    return written
