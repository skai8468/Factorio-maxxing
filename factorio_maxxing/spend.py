"""How much each API key has spent, estimated from the recorded runs.

See docs/decisions.md D56. Every ``llm_call`` record names the key that paid for it by
fingerprint (``key_id``); this totals the calls per key across all trajectories and
prices them with the single pricing table (D55). Nothing here is written anywhere.

    python -m factorio_maxxing.spend               # every run in trajectories/
    python -m factorio_maxxing.spend --openrouter  # plus OpenRouter's own figures

The estimate covers harness runs only - not calls made outside the harness - at list
prices. The provider's console is the bill; ``--openrouter`` asks OpenRouter for its
own usage figures for the current key, which do cover everything that key paid for.
"""

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factorio_maxxing.llm import PROVIDERS, key_fingerprint
from factorio_maxxing.pricing import PRICES_AS_OF, call_cost
from factorio_maxxing.trajectory import read_trajectory

UNTRACKED = "untracked"
"""Calls recorded before D56, or by a client with no key."""

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


@dataclass
class KeySpend:
    key_id: str
    runs: set[str] = field(default_factory=set)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    models: list[str] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)


def total_by_key(records: Iterable[Mapping[str, Any]]) -> list[KeySpend]:
    """Total ``llm_call`` records per key, most expensive first, untracked last."""
    spend: dict[str, KeySpend] = {}
    for record in records:
        if record.get("type") != "llm_call":
            continue
        key_id = str(record.get("key_id") or UNTRACKED)
        entry = spend.setdefault(key_id, KeySpend(key_id))
        model = str(record.get("model", "?"))
        entry.runs.add(str(record.get("run_id", "?")))
        entry.calls += 1
        entry.input_tokens += int(record.get("input_tokens") or 0)
        entry.output_tokens += int(record.get("output_tokens") or 0)
        if model not in entry.models:
            entry.models.append(model)
        cost = call_cost(dict(record))
        if cost is None:
            if model not in entry.unpriced:
                entry.unpriced.append(model)
        else:
            entry.cost += cost
    return sorted(spend.values(), key=lambda s: (s.key_id == UNTRACKED, -s.cost))


def current_keys(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Fingerprints of the keys set now, mapped to their environment variable."""
    environ = os.environ if environ is None else environ
    found = {}
    for provider in PROVIDERS.values():
        key = environ.get(provider.api_key_env)
        if key:
            found[key_fingerprint(provider.api_key_env, key)] = provider.api_key_env
    return found


def read_records(directory: Path) -> tuple[list[dict[str, Any]], int]:
    """Every record of every trajectory in ``directory``, and how many files were read."""
    records: list[dict[str, Any]] = []
    paths = sorted(directory.glob("*.jsonl"))
    for path in paths:
        try:
            records.extend(read_trajectory(path))
        except (OSError, ValueError):
            continue
    return records, len(paths)


def format_spend(
    spend: Sequence[KeySpend], current: Mapping[str, str], files: int, directory: Path
) -> list[str]:
    lines = [
        f"spend by API key - estimated from {files} trajectories in {directory}, "
        f"at list prices as of {PRICES_AS_OF}"
    ]
    if not spend:
        return [*lines, "  no model calls recorded"]
    for entry in spend:
        if entry.key_id == UNTRACKED:
            name = "untracked (runs before key tracking, or keyless)"
        else:
            name = entry.key_id + (" (set now)" if entry.key_id in current else "")
        cost = f"~${entry.cost:.2f}"
        if entry.unpriced:
            cost += f" + unpriced {', '.join(entry.unpriced)}"
        lines.append(f"  {name}")
        tokens = f"in {entry.input_tokens:,}, out {entry.output_tokens:,}"
        lines.append(
            f"      {len(entry.runs)} runs, {entry.calls} calls, {tokens} | {cost}"
            f" | {', '.join(entry.models)}"
        )
    total = sum(entry.cost for entry in spend)
    lines.append(
        f"  total ~${total:.2f} (harness runs only; the provider's console is the bill)"
    )
    return lines


def openrouter_usage(
    key: str, fetch: Callable[[urllib.request.Request], bytes] | None = None
) -> dict[str, Any]:
    """OpenRouter's own usage figures for one key, from GET /api/v1/key."""
    request = urllib.request.Request(
        OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"}
    )
    if fetch is None:

        def fetch(req: urllib.request.Request) -> bytes:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.read()

    return json.loads(fetch(request)).get("data", {})


def format_openrouter(key_id: str, data: Mapping[str, Any]) -> list[str]:
    """OpenRouter's figures, in its own unit. The key's label is not printed: by default
    it is a masked form of the key itself."""

    def credits(name: str) -> str:
        value = data.get(name)
        return "-" if value is None else f"{float(value):.4f}"

    remaining = data.get("limit_remaining")
    return [
        f"openrouter, reported by OpenRouter for {key_id} (credits):",
        f"  used today {credits('usage_daily')}, this week {credits('usage_weekly')}, "
        f"this month {credits('usage_monthly')}, all time {credits('usage')}",
        "  remaining "
        + ("unlimited" if remaining is None else f"{float(remaining):.4f}"),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factorio_maxxing.spend",
        description="Estimated spend per API key across recorded runs.",
    )
    parser.add_argument("--dir", default="trajectories", help="trajectory directory")
    parser.add_argument(
        "--openrouter",
        action="store_true",
        help="also ask OpenRouter for its usage figures for the current key",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    directory = Path(args.dir)
    records, files = read_records(directory)
    current = current_keys()
    for line in format_spend(total_by_key(records), current, files, directory):
        print(line)

    if args.openrouter:
        env = PROVIDERS["open-router"].api_key_env
        key = os.environ.get(env)
        print("")
        if not key:
            print(f"openrouter: {env} is not set", file=sys.stderr)
            return 2
        try:
            data = openrouter_usage(key)
        except (OSError, ValueError) as error:
            print(f"openrouter: could not read usage ({error})", file=sys.stderr)
            return 2
        for line in format_openrouter(key_fingerprint(env, key), data):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
