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
    open_ended: bool = False  # no finish line: never verified, ends at max_steps (D67)

OPEN_ENDED_DESCRIPTION = ("Progress as far as you can up the technology tree, "
                          "automating production as you go.")
def open_ended_goal(max_steps: int, notes: str | None = None) -> Goal: ...

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
whitespace-only `description`, or for `max_steps < 1`.

An open-ended goal is never passed to the verifier, and the intervention cap never
aborts it: `run_goal` accepts `verifier=None` for it (and raises `ValueError` for
`None` on any other goal), and returns `completed=False`, `reason="max_steps reached"`
at the cap unless the environment ends the run first. `GoalResult` does not
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
    key_id: str | None = None   # "<KEY_ENV>:<8 hex of sha256(key)>", never the key (D56)

@dataclass(frozen=True)
class Prompt:
    cached: str   # stable leading part a provider may cache
    rest: str     # str(prompt) == f"{cached}\n\n{rest}" (or rest alone)

class LLMClient(Protocol):
    def generate(self, prompt: str | Prompt) -> LLMResponse: ...
```

Raw usage only — no computed cost (see `decisions.md` D9). `input_tokens` counts **every**
input token the model processed, cached or not; `cache_read_tokens` and
`cache_write_tokens` are shares of it, so the uncached share is
`input_tokens - cache_read_tokens - cache_write_tokens`. The policy prompt marks the API
reference as its cached part; a client that cannot cache sends `str(prompt)`, the exact
same text (`decisions.md` D51).
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

Two compositions: `default_detector(threshold, non_done_threshold)` - repeated errors
plus consecutive non-DONE verdicts - and, on an open-ended goal,
`open_ended_detector(threshold, backstop_steps)` - repeated errors plus
`StepBackstopDetector`, which fires after `backstop_steps` steps without a request for
help (reason `"N steps without a request for help"`) (D67).

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
def render_observation(obs: dict, *, max_entities=150, include_entities=True) -> str: ...
```

Emits compact labelled sections: `INVENTORY`, `CHARACTER` (position), `ENTITIES`,
`RESEARCH`, `FLOWS`, `FUNCTIONS` (the agent's saved functions), `GAME` (tick, game time,
production score), `EXECUTION` (stdout/stderr). Never sends raw FLE observations to a
model.

`ENTITIES` lists every entity, one line each: name, position, direction, status, then
fuel, contents, recipe, research, resources mined, pickup/drop positions and warnings
(D66). Above `max_entities`, entities without a problem collapse by name and status;
problems are always detailed first. `include_entities=False` replaces the list with a
one-line "omitted" note - the form history entries use.

## `context.py`

```python
def build(goal, rendered_observation, history, guidance, errors,
          *, api_reference="", history_length=8, report="", report_until=None) -> str: ...
```

Section order: `HOW TO PLAY` and `ENVIRONMENT API` (the cached part), `GOAL`,
`HISTORY REPORT (steps 0-N)`, `RECENT HISTORY`, `EXECUTION ERRORS`,
`CURRENT OBSERVATION`, `HUMAN GUIDANCE`, `INSTRUCTIONS`. `HOW TO PLAY` is adapted from
FLE's `GYM_AGENT_INSTRUCTIONS` (D66). `guidance` is a **list** that accumulates across
the goal, rendered most recent last. History window is 8 steps, each rendered without
entities; older steps reach the policy through the history report.

## `history_report.py`

```python
REPORT_INTERVAL = 8
class HistoryReporter:
    def __init__(self, client: LLMClient, interval: int = REPORT_INTERVAL): ...
    def due(self, step: int) -> bool: ...          # after steps 7, 15, 23, ...
    def update(self, previous: str, steps, current_observation: str) -> LLMResponse: ...
```

The policy's own client rewrites the report from the previous report, the steps since
(`(step, program, result)`) and the current observation. Its call is recorded as an
`llm_call` with role `summarizer`; a failed call keeps the previous report and the run
continues (D66).

## `trajectory.py`

JSONL, append-friendly. Five record types. Every record carries `type`, `run_id` and
`step`; `type` is the only discriminator.

```json
{"type":"run","run_id":"...","step":null,"goal":"...","open_ended":true,
 "max_steps":300,"config":{...every Config field...},
 "code":{"commit":"<sha or null>","dirty":false}}

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

{"type":"report","run_id":"...","step":7,"text":"..."}
```

The `run` record (D70) is written once, first, by `run.py`: the goal, the full
resolved `Config` (no secrets - keys never enter it) and the code's git commit with
whether it had uncommitted changes. It has no step. Readers that count steps or calls
ignore it. A trajectory recorded before D70 has none.

A `report` record (D66) holds a history report verbatim, covering steps 0 to `step`;
the policy reads it from the next step on. Its call is an `llm_call` with role
`summarizer`.

All model usage - policy, verifier, and every future LLM caller - is recorded as
`llm_call` records distinguished by `role`. Token analysis is therefore permanently
`sum(r["input_tokens"] for r in records if r["type"] == "llm_call")`, and no schema
widening is needed when a new caller arrives (see `decisions.md` D22). Pricing a run
prices the uncached, cache-read and cache-write shares separately (D51). The single pricing table
is `factorio_maxxing/pricing.py`; `run.py` prints each run's estimated cost from it when
the run ends, and `python -m factorio_maxxing.pricing <trajectory>` prices any recorded
run. The estimate is printed, never written to a trajectory (D55). The same table
drives the run-time spend ceiling: `pricing.SpendMeter(ceiling_usd)` prices each call
as `run_goal(..., spend=meter)` records it, keeps the total in memory, and the loop ends
the run with `reason="spend ceiling reached"` once `meter.exceeded()` (D68). `llm_call` records carry
`key_id` - the paying key's fingerprint - whenever the client used a key, and
`python -m factorio_maxxing.spend` totals the estimate per key across all trajectories
(D56).

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

## `scorer.py`

Reads a finished trajectory; never runs in the loop (`decisions.md` D62).

```python
LADDER_VERSION: str                 # stamped on every Score; bump on any ladder change

@dataclass(frozen=True)
class Milestone:
    key: str                        # e.g. "automated-iron"
    description: str
    check: Callable[[dict, History], bool]   # one observation + what was seen so far

@dataclass(frozen=True)
class Score:
    run_id: str | None
    steps: int                      # step records read
    reached: dict[str, int]         # milestone key -> first recorded step it held
    highest: int                    # 1-based rung of the highest milestone; 0 = none
    ladder_version: str

def score_records(records, ladder=LADDER) -> Score
def score_trajectory(path) -> Score
```

Reads `step` records' `observation` only. `verification` records are ignored by
construction, so no verdict can change a score. A milestone stays reached once it has
held. `python -m factorio_maxxing.scorer TRAJECTORY.jsonl ...` prints the ladder with the
first step of each rung.

## `annotations.py`

One JSON object per stuck event in `annotations/<run_id>.jsonl`, keys in this order (D69):

```json
{"run_id": "...", "step": 9, "intervention_index": 0,
 "stuck_reason": "10 consecutive non-DONE verifications",
 "policy_model": "claude-haiku-4-5", "grid_cell": "pilot", "harness": "standard",
 "human": true, "error_category": "E05", "error_secondary": "E03", "error_note": "...",
 "guidance_category": "G2", "guidance_secondary": "G1", "guidance_text": "...",
 "guidance_mentions_api": true, "unstuck": "yes", "unstuck_k": 10,
 "ladder_version": "v0-draft", "codebook_version": "v0", "annotator": "...",
 "notes": null}
```

- *From the trajectory, verbatim:* `intervention_index`, `stuck_reason`, `guidance_text`
  (the intervention record's `text`; null when the request went unanswered) and
  `policy_model` (the first `policy` llm_call's model).
- *From the Scorer:* `unstuck` - `yes` if a milestone is first reached at a step in
  `(step, step + unstuck_k]`, else `n/a` if the run's last step is before `step +
  unstuck_k`, else `no` - stamped with `ladder_version`.
- *From the annotator:* `grid_cell` (`1`-`8`, `5'`-`8'` or `pilot`), `harness`
  (`standard` / `specialized`), `human`, the codes (`E01`-`E09`, `G1`-`G5`, codebook v0),
  notes and `annotator`. With no guidance text the guidance fields are null; with it,
  a primary guidance code and `guidance_mentions_api` are required. A secondary code
  may not repeat its primary; `E09` needs an `error_note`, `G5` a `notes`.

`validate(rows, run_facts(trajectory_records), complete=True) -> list[str]` returns the
problems, empty when valid; `complete` requires exactly one row per intervention record.
`python -m factorio_maxxing.annotations export [WORKBOOK]` writes one file per annotated
run (none for a run with a bad row) and `... validate FILE...` checks files; both exit 1
on any problem. No control-flow module imports this one.

## Config

```json
{
  "policy_model": "claude-haiku-4-5",
  "verifier_model": "claude-haiku-4-5",
  "human": "interactive",
  "stuck_detector": "consecutive_failures+error_signature",
  "stuck_threshold": 3,
  "non_done_threshold": null,
  "max_interventions_without_progress": 3,
  "verification_interval": 1,
  "max_steps": 32,
  "open_ended": false,
  "backstop_steps": 20,
  "max_spend_usd": 50.0,
  "grid_cell": "",
  "harness": "standard",
  "history_length": 8,
  "history_report_interval": 8,
  "environment": "mock",
  "trajectory_dir": "trajectories",
  "dump_prompts": "",
  "api_reference": ""
}
```

Live-only keys (`task_key`, `starting_inventory`, `enable_vision`, `pause_after_action`,
`game_speed`, `fast_mode`, `step_timeout`, `peaceful`) are omitted here;
`configs/harness.example.json` carries every key.

`history_length` is the verbatim history window and `history_report_interval` the steps
between history reports, 0 for none (D66). `dump_prompts` names a directory to write
every prompt the policy's model receives into, one file per call; empty writes nothing.

`non_done_threshold` splits the default detector's single threshold: `stuck_threshold`
governs repeated errors, `non_done_threshold` consecutive non-DONE verdicts. `null` uses
`stuck_threshold` for both, as before. Long goals set it higher (`decisions.md` D50).

`open_ended` runs the open-ended evaluation goal (CLI `--open-ended`, exclusive with
`--goal`): no verifier is built, and `verifier_model`, `verification_interval`,
`non_done_threshold` and `max_interventions_without_progress` have no effect.
`backstop_steps` is its stuck backstop (D67).

`grid_cell` labels a grid run (`1`-`8`, `5'`-`8'`, `pilot`, or `""` outside the grid)
and `harness` names its harness (`standard`; `specialized` is refused until E2). Neither
changes the run; both reach the `run` record. The grid's configs are
`configs/grid/cell-*.json` (D70).

`max_spend_usd` (CLI `--max-spend-usd`) stops a run once its estimated spend reaches it;
`null` turns the ceiling off. With a ceiling set, a policy or verifier model missing
from the pricing table is refused before the run starts; the offline `stub` is exempt
(D68).

API keys come from environment variables, never this file. For an identity-linked API
key, `ANTHROPIC_WORKSPACE_ID` must be set alongside the key (`decisions.md` D27).

`api_reference` is a path to a file describing the functions the environment provides,
rendered to the policy as an `ENVIRONMENT API` section. Empty means the policy is told
nothing about the API - correct against the mock, which ignores submitted code, and
wrong against live Factorio (`decisions.md` D28). Added after build-plan section 12 was
written; that section lists the other eleven keys.

The shipped reference is `configs/fle_api_reference.md`, generated from the installed FLE
by its own `SystemPromptGenerator` (`decisions.md` D31). The default stays `""` because a
relative-path default fails for any run started outside the repository root;
`configs/harness.example.json` sets it, and so must any live run.
