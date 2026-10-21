"""Follow a run from a terminal: one line per step, no game connection needed.

The panel's numbers without the panel - for a run nobody is watching in the game. It
tails the newest trajectory exactly as the watcher's panel does (``TrajectoryTail``,
``HudState``) and prints a line whenever a step is recorded, plus a line when the run
asks for help and when the answer arrives. It only reads; it never touches the run.

    python -m factorio_maxxing.progress            # follow the run in progress
    python -m factorio_maxxing.progress --once     # print where it is now and exit
"""

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

from factorio_maxxing.pricing import summarize
from factorio_maxxing.watch import (
    DEFAULT_TRAJECTORY_DIR,
    HudState,
    TrajectoryTail,
)

DEFAULT_INTERVAL = 1.0
ERROR_CHARS = 100


def status_line(state: HudState) -> str:
    """One line: step against the cap, milestone (highest and contiguous), estimated
    spend, help requests, and the step's error if it had one."""
    step = "-" if state.step is None else str(state.step + 1)
    if state.max_steps:
        step += f"/{state.max_steps}"
    score = state.scorer.score()
    ladder = len(state.scorer.ladder)
    milestone = f"milestone {score.highest}/{ladder}"
    if score.contiguous != score.highest:
        milestone += f" (contiguous {score.contiguous})"
    cost = sum(usage.cost for usage in summarize(state.llm_calls))
    research = f"research {score.research_count} ({score.research_units:,} units)"
    parts = [f"step {step}", milestone, research, f"~${cost:.2f}"]
    if state.help_requests:
        parts.append(f"help asked {state.help_requests}x")
    if state.error:
        error = " ".join(state.error.split())
        if len(error) > ERROR_CHARS:
            error = error[: ERROR_CHARS - 3] + "..."
        parts.append(f"error: {error}")
    return " · ".join(parts)


def follow(
    tail: TrajectoryTail,
    out: Callable[[str], None] = print,
    *,
    once: bool = False,
    interval: float = DEFAULT_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
    ticks: int | None = None,
) -> HudState:
    """Print progress as the trajectory grows; with ``once``, the current state only.

    On attaching to a run already under way it prints one line for where the run is,
    not one per step already taken.
    """
    state = HudState()
    shown_step: int | None = None
    shown_waiting: tuple[int, str] | None = None
    count = 0
    while ticks is None or count < ticks:
        switched, records = tail.poll()
        if switched:
            state, shown_step, shown_waiting = HudState(), None, None
            out(f"following {tail.path.name}")
        if records:
            state.feed(records)
        if state.step is not None and state.step != shown_step:
            out(status_line(state))
            shown_step = state.step
        if state.waiting != shown_waiting:
            if state.waiting is not None:
                at, reason = state.waiting
                out(f"  ⚠ asking for help at step {at + 1}: {reason}")
            elif shown_waiting is not None:
                answer = state.human[1] if state.human else None
                out("  answered" if answer else "  no guidance")
            shown_waiting = state.waiting
        if (
            state.max_steps
            and state.step is not None
            and state.step + 1 >= state.max_steps
        ):
            out("run reached its step cap")
            break
        count += 1
        if once:
            if tail.path is None:
                out("no trajectory yet")
            break
        sleep(interval)
    return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factorio_maxxing.progress",
        description="Follow the run in progress: one line per step.",
    )
    parser.add_argument(
        "--trajectory-dir",
        default=str(DEFAULT_TRAJECTORY_DIR),
        help="where runs write trajectories (default: this repository's trajectories/)",
    )
    parser.add_argument(
        "--once", action="store_true", help="print where the run is now and exit"
    )
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("error: --interval must be positive", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tail = TrajectoryTail(Path(args.trajectory_dir), since=0)
    try:
        follow(tail, once=args.once, interval=args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
