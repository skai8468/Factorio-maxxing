# Factorio-maxxing

Human-Assisted Factorio Harness (FYP).

> How much human assistance does an LLM agent need to climb the Factorio technology
> tree, and can harness engineering reduce that requirement?

See `docs/build-plan.md` for scope, milestones, and build order. `CLAUDE.md` holds the
project-wide rules and architecture invariants.

## Development setup

Requires Python 3.13+.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux / WSL2
```

Run the checks:

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
```

Phases 1-4 run entirely offline: no API key, no Docker, no WSL2, no Factorio.
