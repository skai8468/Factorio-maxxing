"""Estimated cost of a run, priced at analysis time from raw token counts.

See docs/decisions.md D9 and D55. Trajectories store raw usage only and never a dollar
figure, so they stay re-priceable when prices change; this module is the single pricing
table, and what it computes is printed, never recorded.

``input_tokens`` counts every input token, cached or not (D51), so a call is priced as
uncached input (``input - cache_read - cache_write``) plus cache reads, cache writes and
output, each at its own rate.

Usable on any trajectory, including one from an interrupted run::

    python -m factorio_maxxing.pricing trajectories/<run>.jsonl
"""

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factorio_maxxing.trajectory import read_trajectory


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    """Five-minute cache writes, 1.25x input - the only TTL the harness requests."""


PRICES_AS_OF = "2026-10-08"
"""When the table was last checked against Anthropic's list prices. OpenRouter lists the
same per-token prices for these models (D54); its own fees are not modelled."""

PRICES: dict[str, Price] = {
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_read=0.10, cache_write=1.25),
    "claude-sonnet-5-5": Price(
        input=2.00, output=10.00, cache_read=0.20, cache_write=2.50
    ),
    "claude-sonnet-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "claude-opus-5": Price(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-fable-5-1": Price(
        input=10.00, output=50.00, cache_read=0.25, cache_write=12.50
    ),
}

ROLES = ("policy", "verifier")
"""Printed first, in this order; any other role follows alphabetically."""


def price_key(model: str) -> str:
    """Map a configured model string to its pricing-table key.

    ``open-router-anthropic/claude-haiku-4.5`` and ``claude-haiku-4-5`` are the same
    model at the same price: the routing prefix and vendor are dropped and OpenRouter's
    dotted version becomes Anthropic's dashed one.
    """
    key = model.removeprefix("open-router-")
    key = key.split("/", 1)[1] if "/" in key else key
    return key.replace(".", "-")


def call_cost(record: dict[str, Any]) -> float | None:
    """The estimated dollar cost of one ``llm_call`` record, or None if unpriced."""
    price = PRICES.get(price_key(str(record.get("model", ""))))
    if price is None:
        return None
    read = int(record.get("cache_read_tokens") or 0)
    write = int(record.get("cache_write_tokens") or 0)
    uncached = max(int(record.get("input_tokens") or 0) - read - write, 0)
    output = int(record.get("output_tokens") or 0)
    return (
        uncached * price.input
        + read * price.cache_read
        + write * price.cache_write
        + output * price.output
    ) / 1_000_000


@dataclass
class RoleUsage:
    """Raw token totals for one role, with an estimated cost."""

    role: str
    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    models: list[str] = field(default_factory=list)
    cost: float = 0.0
    unpriced: list[str] = field(default_factory=list)
    """Models with no entry in PRICES; their calls are left out of ``cost``."""


def summarize(records: Iterable[dict[str, Any]]) -> list[RoleUsage]:
    """Total each role's ``llm_call`` records, roles in ``ROLES`` order first."""
    usage: dict[str, RoleUsage] = {}
    for record in records:
        if record.get("type") != "llm_call":
            continue
        role = str(record.get("role", "?"))
        entry = usage.setdefault(role, RoleUsage(role))
        model = str(record.get("model", "?"))
        entry.calls += 1
        entry.input_tokens += int(record.get("input_tokens") or 0)
        entry.cache_read_tokens += int(record.get("cache_read_tokens") or 0)
        entry.cache_write_tokens += int(record.get("cache_write_tokens") or 0)
        entry.output_tokens += int(record.get("output_tokens") or 0)
        if model not in entry.models:
            entry.models.append(model)
        cost = call_cost(record)
        if cost is None:
            if model not in entry.unpriced:
                entry.unpriced.append(model)
        else:
            entry.cost += cost
    order = {role: index for index, role in enumerate(ROLES)}
    return sorted(usage.values(), key=lambda u: (order.get(u.role, len(ROLES)), u.role))


def format_summary(usage: Sequence[RoleUsage]) -> list[str]:
    """Terminal lines for a run's usage. Dollar figures are estimates, never recorded."""
    if not usage:
        return ["usage:         no model calls recorded"]
    lines = [f"usage:         estimated at list prices as of {PRICES_AS_OF}"]
    for entry in usage:
        tokens = f"in {entry.input_tokens:,}"
        if entry.cache_read_tokens or entry.cache_write_tokens:
            tokens += (
                f" (cache read {entry.cache_read_tokens:,},"
                f" written {entry.cache_write_tokens:,})"
            )
        tokens += f", out {entry.output_tokens:,}"
        if entry.unpriced:
            cost = f"no price for {', '.join(entry.unpriced)}"
            if entry.cost:
                cost = f"~${entry.cost:.2f} + {cost}"
        else:
            cost = f"~${entry.cost:.2f}"
        lines.append(
            f"  {entry.role:<9}{', '.join(entry.models)}: {entry.calls} calls, "
            f"{tokens} | {cost}"
        )
    total = sum(entry.cost for entry in usage)
    partial = any(entry.unpriced for entry in usage)
    lines.append(
        f"  {'total':<9}~${total:.2f}" + (" (priced calls only)" if partial else "")
    )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print(
            "usage: python -m factorio_maxxing.pricing TRAJECTORY.jsonl ...",
            file=sys.stderr,
        )
        return 2
    for index, path in enumerate(paths):
        if index:
            print("")
        print(f"trajectory:    {path}")
        try:
            records = read_trajectory(Path(path))
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        for line in format_summary(summarize(records)):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
