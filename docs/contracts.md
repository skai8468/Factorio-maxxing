# Contracts

Authoritative Python interfaces. Changing anything here is an architecture change and
requires the research lead's approval (`CLAUDE.md` § Division of responsibility).

## `goal.py`

```python
@dataclass(frozen=True)
class Goal:
    description: str          # free text, e.g. "Build a working iron mining setup."
    max_steps: int = 32
    notes: str | None = None  # optional human hints supplied up front

@dataclass
class GoalResult:
    goal: Goal
    completed: bool
    steps_used: int
    interventions: int
    reason: str               # verifier's stated reason, or abort reason
    trajectory_path: Path
```

Deliberately minimal: no subtasks, no decomposition, no planning graph.

`Goal` validates on construction and raises `ValueError` for an empty or
whitespace-only `description`, or for `max_steps < 1`. `GoalResult` does not
validate: it is a passive record of what happened, including failures.

## `envs.py`

```python
@dataclass(frozen=True)
class Action:
    code: str
    agent_idx: int = 0

class EnvProtocol(Protocol):
    def reset(self) -> dict: ...
    def step(self, action: Action) -> tuple[dict, float, bool, bool, dict]: ...
```

Mirrors FLE's gym signature so `RealFactorioEnv` is a thin wrapper.
`MockFactorioEnv` is scripted by step index and does not interpret submitted Python.

`Action` mirrors `fle/env/gym_env/action.py` so the Phase 5 wrapper is a field-for-field
translation. FLE's third field, `game_state`, is omitted: it is the checkpoint/restore
mechanism and checkpointing is future scope.

`reset()` returns a bare observation `dict`. FLE's own `reset()` returns
`tuple[dict, dict]`; `RealFactorioEnv` unpacks it and discards the `info` mapping,
which no M0/M1 trajectory field consumes. One return shape keeps the loop simple.

## `llm.py`

```python
@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_seconds: float

class LLMClient(Protocol):
    def generate(self, prompt: str) -> LLMResponse: ...
```

Raw usage only — no computed cost (see `decisions.md` D9).
Policy extraction handles fenced ```python blocks, bare Python, and malformed
responses.

## `verifier.py`

```python
@dataclass
class VerificationResult:
    done: bool
    reason: str
```

Receives goal + latest observation + production flows + a short observation/history
window. Never a bare snapshot.

## `stuck.py`

```python
class StuckDetector(Protocol):
    def is_stuck(self, history, verifications, flows, errors) -> tuple[bool, str]: ...
```

Returns `(stuck, reason)`. Decides when to request help; never decides goal completion.

## `human.py`

```python
class HumanProtocol(Protocol):
    def ask(self, goal: Goal, observation: str, reason: str) -> str | None: ...
```

Returns guidance text, or `None` for no assistance. Implementations: `NoHuman`,
`InteractiveHuman`, `ScriptedHuman`.

`ScriptedHuman` matches by **sequence order**: the Nth stuck event receives the Nth
recorded hint. On exhaustion it degrades to `NoHuman` behaviour and logs the
exhaustion.

## `rendering.py`

```python
def render_observation(obs: dict) -> str: ...
```

Emits compact labelled sections: `INVENTORY`, `ENTITIES` (position + status),
`RESEARCH`, `FLOWS`, `EXECUTION` (stdout/stderr). Never sends raw FLE observations to
a model.

## `context.py`

```python
def build(goal, rendered_observation, history, guidance, errors) -> str: ...
```

`guidance` is a **list** that accumulates across the goal, rendered as a labelled
`HUMAN GUIDANCE` section, most recent last. History window is 16 steps.

## `trajectory.py`

JSONL, append-friendly. Four record types. Every record carries `type`, `run_id` and
`step`; `type` is the only discriminator.

```json
{"type":"step","run_id":"...","step":0,"goal":"...","policy":"...",
 "observation":{},"reward":0.0,"execution_errors":[]}

{"type":"llm_call","run_id":"...","step":0,"role":"policy","model":"stub",
 "input_tokens":412,"output_tokens":88,"cache_read_tokens":0,
 "cache_write_tokens":0,"latency_seconds":0.0}

{"type":"llm_call","run_id":"...","step":0,"role":"verifier","model":"stub",
 "input_tokens":260,"output_tokens":21,"cache_read_tokens":0,
 "cache_write_tokens":0,"latency_seconds":0.0}

{"type":"verification","run_id":"...","step":0,"done":false,"reason":"..."}

{"type":"intervention","run_id":"...","step":3,"stuck_reason":"...",
 "text":"...","intervention_index":0,"original_step":3}
```

All model usage - policy, verifier, and every future LLM caller - is recorded as
`llm_call` records distinguished by `role`. Cost analysis is therefore permanently
`sum(r["input_tokens"] for r in records if r["type"] == "llm_call")`, and no schema
widening is needed when a new caller arrives (see `decisions.md` D22).

Required consequences:

- `verification_interval > 1` emits **no** verification record on skipped steps, rather
  than a record with null columns.
- A verifier that makes no API call - `StubVerifier` - emits a `verification` record and
  **no** `llm_call` record. "No call made" stays distinguishable from "call made, zero
  tokens".
- Verdict and usage stay in separate records: `verification` is control-flow evidence,
  `llm_call` is cost evidence. This preserves the verifier/recorder separation in D6.

Intervention text is stored **verbatim** and is the source of truth. Optional
classification (`KNOWLEDGE` / `DIAGNOSIS` / `PLANNING` / `CORRECTION`) is an additional
field only — it never replaces raw text and never affects control flow.

## Config

```json
{
  "policy_model": "claude-haiku-4-5",
  "verifier_model": "claude-haiku-4-5",
  "human": "interactive",
  "stuck_detector": "consecutive_failures+error_signature",
  "stuck_threshold": 3,
  "max_interventions_without_progress": 3,
  "verification_interval": 1,
  "max_steps": 32,
  "history_length": 16,
  "environment": "mock",
  "trajectory_dir": "trajectories"
}
```

API keys come from environment variables, never this file.
