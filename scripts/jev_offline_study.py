"""Offline study: could JEV tell a real stuck event from a backstop false alarm?

Replays every annotated help request through TypeSafe's JEV decision model
(``typesafe/jev-1.13`` via OpenRouter's Decisions endpoint) and compares its
probabilities with the codebook's codes. Nothing here touches the harness: it reads
recorded trajectories and annotations, and writes results under
``docs/studies/jev-offline/``.

JEV sees only what a stuck detector could see at the moment of the request - the last
five programs with their output and errors, and a summary of the last twenty steps
built from the recorded observation. It never sees Scorer milestones (the Scorer may
not drive control flow) or anything after the request.

    # in WSL, with OPEN_ROUTER_API_KEY set (source ~/fle-work/.env.local)
    python scripts/jev_offline_study.py run --repeats 3
    python scripts/jev_offline_study.py analyse
    python scripts/jev_offline_study.py run --limit 1        # smoke test
    python scripts/jev_offline_study.py run --run <run_id> --results held-out.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "studies" / "jev-offline"
RESULTS = OUT_DIR / "results.jsonl"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
RECENT = 5
WINDOW = 20

QUESTIONS = {
    "making_progress": (
        "Across `window` and `recent_steps`, the agent is making progress: its programs "
        "mostly run without errors and its factory, research or inventory is growing."
    ),
    "stuck": (
        "In `recent_steps` the agent is stuck: it keeps failing at the same task, or "
        "repeats the same failing action without materially changing its approach."
    ),
    "environment_fault": (
        "The failures in `recent_steps` come from the game server or harness (timeouts, "
        "connection or RCON errors, a frozen or unresponsive game) rather than from "
        "mistakes in the agent's own code."
    ),
}


# --- state ----------------------------------------------------------------------------


def _cut(text: Any, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text)
    return (
        text if len(text) <= limit else text[:limit] + f" ...[{len(text) - limit} more]"
    )


def _summary(obs: dict[str, Any]) -> dict[str, Any]:
    entities = obs.get("entities") or []
    working = sum(1 for e in entities if "WORKING" in str(e.get("status", "")))
    research = obs.get("research") or {}
    techs = research.get("technologies") or []
    researched = sum(1 for t in techs if isinstance(t, dict) and t.get("researched"))
    items = 0
    for entry in obs.get("inventory") or []:
        try:
            items += int(entry.get("quantity", 0))
        except (TypeError, ValueError):
            pass
    return {
        "entities": len(entities),
        "entities_working": working,
        "technologies_researched": researched,
        "current_research": research.get("current_research"),
        "research_progress": round(float(research.get("research_progress") or 0), 2),
        "inventory_items": items,
        "production_score": obs.get("score"),
    }


def build_state(steps: dict[int, dict[str, Any]], at: int, goal: str) -> dict[str, Any]:
    window = [steps[s] for s in range(max(0, at - WINDOW + 1), at + 1) if s in steps]
    recent = [steps[s] for s in range(max(0, at - RECENT + 1), at + 1) if s in steps]
    return {
        "goal": goal,
        "window": {
            "steps": f"{window[0]['step']}-{window[-1]['step']}",
            "steps_with_errors": sum(1 for r in window if r.get("execution_errors")),
            "at_start": _summary(window[0]["observation"]),
            "at_end": _summary(window[-1]["observation"]),
        },
        "recent_steps": [
            {
                "step": r["step"],
                "program": _cut(r.get("policy") or "", 1500),
                "output": _cut((r.get("observation") or {}).get("raw_text", ""), 800),
                "errors": _cut(r.get("execution_errors") or [], 600),
            }
            for r in recent
        ],
    }


def load_requests(runs: list[str] | None = None) -> list[dict[str, Any]]:
    """Every annotated help request with the state rebuilt from its trajectory,
    optionally only those of the given runs."""
    requests = []
    for path in sorted(glob.glob(str(ROOT / "annotations" / "*.jsonl"))):
        if runs and Path(path).stem not in runs:
            continue
        rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
        if not rows:
            continue
        run_id = rows[0]["run_id"]
        steps, goal = {}, ""
        for line in open(ROOT / "trajectories" / f"{run_id}.jsonl", encoding="utf-8"):
            record = json.loads(line)
            if record["type"] == "step":
                steps[record["step"]] = record
            elif record["type"] == "run":
                goal = record.get("goal", "")
        for row in rows:
            requests.append(
                {
                    "run_id": run_id,
                    "step": row["step"],
                    "stuck_reason": row["stuck_reason"],
                    "code": row["error_category"],
                    "verified": bool(row.get("verified_by")),
                    "state": build_state(steps, row["step"], goal),
                }
            )
    return requests


# --- JEV --------------------------------------------------------------------------------


def ask_jev(state: dict[str, Any], key: str) -> dict[str, Any]:
    import httpx

    body = {
        "model": MODEL,
        "state": state,
        "questions": {
            name: {"type": "noul", "instructions": text}
            for name, text in QUESTIONS.items()
        },
    }
    started = time.monotonic()
    response = httpx.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    seconds = round(time.monotonic() - started, 3)
    response.raise_for_status()
    data = response.json()
    answers = data.get("answers") or {}
    probabilities = {}
    for name in QUESTIONS:
        value = (answers.get(name) or {}).get("noul")
        if not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ValueError(f"JEV did not answer {name!r}: {answers.get(name)!r}")
        probabilities[name] = float(value)
    usage = data.get("usage") or {}
    return {
        "probabilities": probabilities,
        "model": data.get("model"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "seconds": seconds,
    }


def run(repeats: int, limit: int | None, runs: list[str], results: Path) -> int:
    key = os.environ.get("OPEN_ROUTER_API_KEY")
    if not key:
        print("OPEN_ROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    requests = load_requests(runs)[: limit or None]
    results.parent.mkdir(parents=True, exist_ok=True)
    with open(results, "w", encoding="utf-8") as out:
        for request in requests:
            for repeat in range(repeats):
                answer = ask_jev(request["state"], key)
                record = {k: v for k, v in request.items() if k != "state"}
                record.update(
                    repeat=repeat, state_chars=len(json.dumps(request["state"]))
                )
                record.update(answer)
                out.write(json.dumps(record) + "\n")
                p = answer["probabilities"]
                print(
                    f"{request['run_id'][-8:]} step {request['step']:3} "
                    f"{request['code']} "
                    f"r{repeat} progress {p['making_progress']:.2f} "
                    f"stuck {p['stuck']:.2f} env {p['environment_fault']:.2f} "
                    f"({answer['seconds']} s)"
                )
    print(f"wrote {results}")
    return 0


# --- analysis ---------------------------------------------------------------------------


def analyse(results: Path) -> int:
    rows = [json.loads(x) for x in open(results, encoding="utf-8") if x.strip()]
    by_request: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        by_request.setdefault((row["run_id"], row["step"]), []).append(row)

    requests = []
    spreads = []
    for _, group in sorted(by_request.items()):
        mean = {
            q: sum(r["probabilities"][q] for r in group) / len(group) for q in QUESTIONS
        }
        for q in QUESTIONS:
            values = [r["probabilities"][q] for r in group]
            spreads.append(max(values) - min(values))
        requests.append({**group[0], "mean": mean, "n": len(group)})

    def table(subset: list[dict[str, Any]], title: str) -> None:
        e10 = [r for r in subset if r["code"] == "E10"]
        real = [r for r in subset if r["code"] != "E10"]
        print(f"\n{title}: {len(subset)} requests - {len(e10)} E10, {len(real)} real")
        print(
            "  filter rule (skip the human)        E10 filtered   real wrongly filtered"
        )
        rules = [
            (f"making_progress >= {t}", lambda m, t=t: m["making_progress"] >= t)
            for t in (0.5, 0.7, 0.8, 0.9)
        ] + [
            (
                "gate: progress >= 0.9 and stuck <= 0.1",
                lambda m: m["making_progress"] >= 0.9 and m["stuck"] <= 0.1,
            ),
            ("stuck <= 0.1", lambda m: m["stuck"] <= 0.1),
            ("stuck <= 0.3", lambda m: m["stuck"] <= 0.3),
        ]
        for label, rule in rules:
            hit = sum(1 for r in e10 if rule(r["mean"]))
            miss = [r for r in real if rule(r["mean"])]
            names = ", ".join(f"{r['code']}@{r['step']}" for r in miss)
            print(
                f"  {label:38} {hit:3}/{len(e10):<3}        {len(miss)}/{len(real)}"
                + (f"  ({names})" if names else "")
            )

    print(f"calls: {len(rows)}, requests: {len(requests)}, repeats: {requests[0]['n']}")
    seconds = sorted(r["seconds"] for r in rows)
    print(
        f"latency: median {seconds[len(seconds) // 2]:.2f} s, max {seconds[-1]:.2f} s; "
        f"input tokens per call ~{sum(r['input_tokens'] or 0 for r in rows) // len(rows)}"
    )
    print(
        f"repeat spread per question: median {sorted(spreads)[len(spreads) // 2]:.3f}, "
        f"max {max(spreads):.3f}"
    )
    table(requests, "all")
    table([r for r in requests if r["verified"]], "verified only")

    print("\nper request (mean over repeats):")
    for r in requests:
        m = r["mean"]
        print(
            f"  {r['run_id'][-8:]} step {r['step']:3} {r['code']}"
            f"{'' if r['verified'] else '*'}"
            f"  progress {m['making_progress']:.2f}  stuck {m['stuck']:.2f}"
            f"  env {m['environment_fault']:.2f}"
        )
    print("  (* = draft code, not yet verified)")

    env = [r for r in requests if r["code"] == "E08"]
    other = [r for r in requests if r["code"] != "E08"]
    if env:
        print(
            "\nenvironment_fault mean: E08 "
            f"{sum(r['mean']['environment_fault'] for r in env) / len(env):.2f}, "
            "others "
            f"{sum(r['mean']['environment_fault'] for r in other) / len(other):.2f}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="ask JEV about every annotated request")
    run_parser.add_argument("--repeats", type=int, default=3)
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument(
        "--run", action="append", default=[], help="only this run id (repeatable)"
    )
    analyse_parser = sub.add_parser("analyse", help="compare JEV's answers with codes")
    for each in (run_parser, analyse_parser):
        each.add_argument(
            "--results", type=Path, default=RESULTS, help=f"default {RESULTS.name}"
        )
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args.repeats, args.limit, args.run, args.results)
    return analyse(args.results)


if __name__ == "__main__":
    sys.exit(main())
