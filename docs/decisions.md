# Decision Log

Architectural decisions and their reasoning. Append-only; supersede rather than delete.

Each entry: what was decided, why, and what it rules out.

---

## D1 — Drive the FLE gym environment directly; do not subclass `AgentABC`

**Decision.** The harness calls `env.step(Action(code=...))` itself rather than
implementing `fle/agents/agent_abc.py::AgentABC`.

**Why.** `AgentABC` is shaped for FLE's own trajectory runner, which owns the loop.
Owning the loop is the central research contribution of this project — the loop *is*
the artifact being studied. Delegating it to FLE would make the thing under study
inaccessible.

**Consequence.** The prompt format stays reasonably close to FLE's `GymAgent` so a
later baseline comparison remains apples-to-apples.

---

## D2 — Never modify FLE's `GymAgent`

**Decision.** `fle/agents/gym_agent.py` is read-only reference.

**Why.** It is the published baseline this project is measured against. Modifying it
destroys the comparison.

---

## D3 — Model ladder, and develop on Haiku rather than the cheapest model

**Decision.** Ladder is DeepSeek V3 → Claude Haiku 4.5 → Claude Sonnet 5 → Claude
Opus 5. Development uses Haiku 4.5.

**Why.** A 32-step goal costs roughly $0.58 on Haiku, so a hundred development runs is
about $58, against roughly $6 on DeepSeek. That ~$50 saving is not worth the ambiguity
it buys: during M0/M1 every failure must be attributable primarily to harness or
infrastructure problems rather than weak model capability. Developing against a weak
model makes every failure ambiguous.

**Consequence.** Cheaper models enter later as ladder rungs, never as the development
driver. DeepSeek V3 is the low-cost API baseline — on FLE's leaderboard it scored 15.1%
on lab tasks and reached `plastic-bar`.

---

## D4 — Local/open-weight models are out of scope

**Decision.** No Ollama, no local inference, no GPU purchase.

**Why.** Llama 3.3 70B is the strongest open-weights data point on FLE's leaderboard
and reached only `iron-plate` at 5.2% lab-task success. Available hardware fits roughly
a 7–8B model, an order of magnitude below that, which would produce no usable signal
for long-horizon play. The primary workload is API calls; GPU spend buys far less
capability than API spend.

---

## D5 — Generic model/API routing; no model-specific logic in the harness

**Decision.** Model selection is a configuration string. The core harness contains no
provider or model conditionals.

**Why.** FLE's `APIFactory.PROVIDERS` is only a `base_url` + key-env pair per provider,
routed through a generic OpenAI-compatible client; model strings pass through with no
allowlist. Keeping the harness model-agnostic makes the capability ladder a config
sweep rather than a code change.

**Consequence.** Policy and verifier models are independently configurable
(`--policy-model` / `--verifier-model`), enabling the question of how much verifier
capability matters relative to policy capability.

---

## D6 — Verifier and recorder are separate

**Decision.** The verifier is LLM-based and drives control flow. The recorder is
passive, records objective state, and never determines success.

**Why.** Preserves the ability to compare "verifier said DONE" against "FLE state
actually shows success" — itself a research result about whether LLM self-verification
tracks objective success in a complex interactive environment. Collapsing them would
make that question unanswerable.

**Consequence.** The verifier receives goal + latest observation + production flows +
a short window, not a bare snapshot. Sustained-production goals cannot be judged from
one tick; FLE's own lab tasks sleep 60s and measure throughput for this reason.

---

## D7 — Stuck detection is separate from verification, and pluggable

**Decision.** `StuckDetector` decides *when to request human help*. It never decides
goal completion.

**Why.** They answer different questions. Conflating them means either asking for help
whenever the goal is incomplete (constant interruption) or never asking at all.

**Consequence.** Consecutive non-DONE must not be the sole signal — an agent can make
legitimate progress while the goal remains incomplete, and a detector firing on that
interrupts a healthy build. Paired with a repeated-error-signature detector from the
outset. The detector runs every step and treats verifications as a sparse signal,
counting verification *events* rather than steps.

---

## D8 — Three human backends

**Decision.** `NoHuman`, `InteractiveHuman`, `ScriptedHuman` behind one interface.

**Why.** `NoHuman` provides the autonomous baseline; the delta between it and
`InteractiveHuman` over an identical goal set *is* the M1 result. `ScriptedHuman` is a
methodological safeguard: replaying one intervention set against two harness versions
lets improvement be attributed to the harness rather than to a better human hint.
Without `NoHuman` and `ScriptedHuman`, unattended multi-seed runs are impossible
because every run would need a human present.

**Consequence.** `ScriptedHuman` matches by **sequence order, never step index**. If
harness A got stuck at steps 7/15/22 and harness B at 9/20, step-keyed replay hands B
nothing and the safeguard fails silently. Both `intervention_index` and `original_step`
are recorded; exhaustion degrades to `NoHuman`.

---

## D9 — Store raw token counts, never computed cost

**Decision.** Trajectories record `model`, `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `latency_seconds`. Dollar cost is derived at
analysis time from a single pricing table.

**Why.** The headline metric is per-dollar performance, and API pricing changes.
Baking cost into records makes historical trajectories unre-priceable, silently
corrupting longitudinal comparisons.

---

## D10 — Offline-first; the mock is a fixture, not a simulator

**Decision.** `MockFactorioEnv` is scripted by step index and does not interpret
submitted Python.

**Why.** Actions are arbitrary Python, not symbolic commands. Deciding transitions
would require parsing arbitrary code — i.e. building a fake Factorio, a rabbit hole
with no research value. Real Factorio-specific behaviour belongs in FLE.

**Consequence.** The mock tests loop control, parsing, rendering, verification
plumbing, stuck detection, human backends, trajectory recording, and replay. Harness
*behaviour* is tuned against the real server, because the failures that matter are
Factorio-specific: belt orientation, drill drop positions, power coverage, fuel
starvation.

---

## D11 — `context.py` as a distinct module

**Decision.** Context assembly (goal + observation + history + guidance + execution
errors) lives in its own module rather than inside `loop.py`.

**Why.** It is the component later experiments will vary most — compact vs full
observations, history compression, guidance injection. Inside `loop.py`, every prompt
experiment becomes a diff against control flow, which is the part that must stay
stable.

**Status.** A deliberate deviation from the originally specified file list, ratified by
the research lead. Kept small: one job, no abstraction beyond it.

---

## D12 — Intervention lifecycle

**Decision.** A successful intervention resets the stuck counter. A separate counter,
`interventions_without_progress`, is *not* reset and aborts the goal at its threshold.
Guidance accumulates as a list and persists until the goal ends.

**Why.** Without the reset, the counter remains above threshold and the detector
re-fires on the very next step, producing an intervention storm. Without the second
counter, an unsolvable goal burns `max_steps` and the operator's patience. Guidance as
a single slot lets a later hint silently erase an earlier one that is still true.

---

## D13 — Goal success rate as the M0/M1 capability metric

**Decision.** Tech-tree depth is the primary capability metric from M3a onward. At
M0/M1 its stand-in is goal success rate, reported separately per human backend.

**Why.** M0/M1 does not touch the tech tree, so tech depth is structurally
unmeasurable there. Intervention count alone is insufficient — an agent needing zero
interventions but making no progress is not better than one needing several and
advancing. Capability and assistance metrics are always read as a pair.

---

## D14 — Toolchain: Python 3.13 floor, hatchling, venv + pip, ruff excludes Markdown

**Decision.** `requires-python = ">=3.13"`. Build backend is `hatchling`. Development
uses a plain `venv` and `pip install -e ".[dev]"`. No runtime dependencies. Ruff is
configured with `extend-exclude = ["*.md"]`.

**Why.** The 3.13 floor is the research lead's call; `fle-integration.md` records FLE
as Python 3.10+, so 3.13 sits inside FLE's stated support and can be lowered in one
line if that turns out to be wrong. `uv` is not installed on the development machine
and build-plan §23 only recommends it for the Phase 5 live setup — the `pyproject.toml`
is standard PEP 621, so `uv sync` works unchanged when development moves into WSL2.
Ruff 0.16 formats Python code blocks inside Markdown, which would rewrite
`docs/build-plan.md`; documentation is authority rank 2 and a linter must never edit
it.

**Consequence.** Phases 1–4 install nothing beyond `pytest` and `ruff`. FLE becomes an
optional extra at Phase 5, not a base dependency, keeping the offline loop installable
on a machine with no Docker or WSL2.

---

## D15 — `Action` is defined locally; the mock repeats its final frame

**Decision.** `envs.py` defines its own frozen `Action(code, agent_idx=0)` mirroring
`fle/env/gym_env/action.py`, omitting FLE's `game_state` field.
`MockFactorioEnv(reset_observation, frames)` returns the Nth `MockFrame` on the Nth
`step()`, and repeats the final frame once the script is exhausted. `reset()` rewinds
the index and returns a bare observation `dict`.

**Why.** `contracts.md` already referenced `Action` in the `EnvProtocol` signature
without defining it, and Phases 1-4 install no FLE dependency, so the type has to exist
locally for the offline loop to run at all. Mirroring FLE's field names keeps the
Phase 5 `RealFactorioEnv` a field-for-field translation rather than an adapter.
`game_state` is omitted because it is the checkpoint/restore mechanism, which is future
scope.

Repeating the final frame lets a three-frame fixture back a 32-step run without
inventing transitions the fixture author never wrote. Raising on exhaustion would force
every `max_steps` test to script 32 frames; both options are deterministic, and this one
keeps fixtures small. The mock never terminates a run on its own account beyond what its
frames declare - loop termination is `loop.py`'s job.

**Consequence.** `submitted_actions` retains what was submitted for test inspection
only; it never influences a transition. `RealFactorioEnv` is not stubbed - it arrives at
Phase 5 (build-plan section 19, item 17).

---

## D16 — Rendering is defensive, always emits every section, and caps entity lines

**Decision.** `render_observation` always emits all five headers, using `(none)` where
the observation carries nothing. It reads FLE keys defensively - a missing, `None`, or
wrong-typed field renders as empty rather than raising. Entity lines are capped at
`MAX_ENTITIES = 32`, with the remainder summarised as `... and N more`. `price_list` is
never rendered.

**Why.** A stable section structure across steps means the policy is not re-reading a
differently shaped prompt each turn, and a renderer that raises on an unexpected
observation would abort a run for a cosmetic reason. The cap matters because a mature
base carries hundreds of entities: uncapped, ENTITIES alone would dominate the context
window that history and guidance also have to fit into. `price_list` is large and
irrelevant to any M0/M1 goal.

**Unverified.** The exact `research` and `flows` sub-keys come from the `Observation`
dataclass recorded in `fle-integration.md`, not from a live gym observation dict. The
renderer accepts several spellings (`current_research`/`current`,
`research_progress`/`progress`) and degrades to `(none)` on anything unrecognised.
Confirm against a real observation at Phase 5 and tighten then.

**Consequence.** `max_entities` is a keyword argument, so observation compactness is
already a knob that a later context experiment can sweep without editing the module.

---

## D17 — A response with no usable code extracts to empty, not an exception

**Decision.** `extract_policy` returns `""` for a response carrying no usable code.
It never raises. Where several fenced blocks are present the last is taken. Fenced code
is returned verbatim without a syntax check; unfenced text must parse as Python to count
as code at all. An unterminated fence, left by a response truncated at `max_tokens`,
still yields its contents.

**Why.** Raising would turn a model failure into control flow the loop must catch and
translate. Returning empty code keeps the failure inside the trajectory: the step
executes nothing, and the reason is visible in the recorded response and the EXECUTION
section, where the error taxonomy and the repeated-error-signature stuck detector can
both see it. Approved by the research lead.

Taking the last block matches how models write - reasoning, plans and worked examples
come before the final answer - and matches FLE's `GymAgent` format, which puts its
POLICY stage last. Not syntax-checking fenced code is deliberate: the model meant it as
code, so it should reach the environment and have its `SyntaxError` fed back rather than
be silently discarded. The `ast.parse` check applies only to unfenced text, where it is
the one available signal separating bare Python from prose.

**Consequence.** An empty policy is a legitimate recorded value. `loop.py` and
`trajectory.py` must both treat `""` as data, not as a missing field.

---

## D18 — Prompt section order, and the policy instructions live in `context.py`

**Decision.** `build` emits GOAL, RECENT HISTORY, EXECUTION ERRORS, CURRENT OBSERVATION,
HUMAN GUIDANCE, INSTRUCTIONS, in that order. History, errors and guidance sections are
omitted entirely when empty; GOAL, CURRENT OBSERVATION and INSTRUCTIONS are always
present. History steps are numbered from zero, matching the loop and the trajectory.
The policy instruction text is a module constant, `POLICY_INSTRUCTIONS`.

**Why.** HUMAN GUIDANCE sits last before the instructions because it is the object of
study: if guidance is buried above sixteen steps of history, a null result becomes
impossible to attribute between "the human's hint was useless" and "the model never
really saw it". Absent context is omitted rather than rendered as `(none)` because an
empty section is not information - this differs from `rendering.py`, where a stable
section skeleton across steps is the point.

The instructions are structurally comparable to FLE's `GYM_AGENT_INSTRUCTIONS` - a
planning stage, then one fenced Python block, with a 50-line guideline and a
do-not-repeat reminder - but the wording is ours, not copied from FLE.

**Consequence.** `history_length` is a keyword argument, so the history window is
already a sweepable experiment parameter (build-plan section 18) without editing the
module.

---

## D19 — An unreadable verdict is NOT DONE; the window carries flows

**Decision.** `parse_verdict` reads NOT DONE before DONE, and returns NOT DONE for an
empty or unparseable response, keeping the raw text as the reason. `check(goal,
observation, window)` takes `window` as recent rendered observations in step order, most
recent last, rather than a separate flows structure. `LLMVerifier` retains
`last_response` so verifier usage can be attributed separately from policy usage.

**Why.** The failure modes are asymmetric. A false NOT DONE costs some steps; a false
DONE ends the goal, records a success that did not happen, and corrupts the goal
success rate that is the M0/M1 capability metric (D13). Defaulting to NOT DONE keeps the
cheap error. Reading NOT DONE first matters because the string "NOT DONE" contains
"DONE", so naive matching inverts the verdict in exactly the case that must not be got
wrong.

Rendered observations already carry a FLOWS section, so passing a window of them
satisfies "production flows plus a short window" (D6) through one path instead of two.
A second parallel flows argument would let the two disagree.

**Consequence.** `VERIFICATION_WINDOW = 4` is a default, not a contract; the loop passes
what it holds. Whether verifier tokens are recorded as their own trajectory fields is an
open question for item 11 - the seam exists, the schema decision does not.

---

## D20 — Detectors are pure; the loop owns the intervention reset

**Decision.** Detectors are pure functions of `(history, verifications, flows, errors)`
and hold no counters. D12's "an intervention resets the stuck counter" is implemented in
`loop.py`, which after an intervention passes only the events recorded since it.
`errors` carries one entry per step, empty where the step ran cleanly. `flows` is
accepted and currently unused - the flat-flow detector is future scope.

**Why.** The contract signature is already a pure function of its inputs, and putting a
second counter inside the detector would give the loop and the detector separate ideas
of when the window began - the sort of divergence that produces an intervention storm or
a detector that never fires again, both of which look like harness bugs in the results.
`architecture.md` already places the counters in `loop.py`; this keeps them there.

One error entry per step is what makes a clean step break the streak. Without it, an
agent that fails, recovers, then fails differently would read as looping, and the
repeated-error detector would fire on a healthy build.

**Interpretation to confirm.** Build-plan section 21 requires that the detector "does
not fire during legitimate incomplete progress". This is implemented as: below
threshold, nothing fires - a build making progress across two non-DONE verifications is
left alone. It is *not* implemented as a progress heuristic over production flows,
because flat-flow detection is listed as future scope and explicitly not to be built
now. If the stronger reading was intended - suppress the consecutive detector while
flows are rising - that is a scope decision for the research lead, not a change to make
unilaterally.

**Consequence.** Mapping the config string `consecutive_failures+error_signature` onto
`default_detector()` belongs to `run.py` at item 14, not here.

---

## D21 — Human backends: verbatim text, injected I/O, and hints that carry their origin

**Decision.** `ask` returns the operator's text unaltered. `InteractiveHuman` takes
`input_fn` and `output_fn` as constructor arguments, reads lines until a blank line, and
treats a blank first line, EOF and interrupt alike as "no assistance". `ScriptedHuman`
accepts `Hint(text, original_step)` or plain strings, matches by sequence order, exposes
`next_intervention_index` and `last_hint` for the recorder, and reports coverage through
`usage_report()` / `log_usage()`. Exhaustion and underuse are logged through the standard
`logging` module.

**Why.** Intervention text is the source of truth, so no backend strips, normalises or
reformats it - only the decision of whether a line is blank uses a stripped copy.
Injected I/O keeps `InteractiveHuman` testable without a terminal, which matters because
it is the backend that actually runs the M1 experiments. Swallowing EOF and interrupt
prevents a closed stdin from killing a run that has already spent real API budget.

`original_step` travels with the hint rather than being looked up, because the recorder
needs both numbers at the moment of replay: `intervention_index` says which hint this
was, `original_step` says when it was needed in the run that produced it. Only the first
is used for matching.

**Consequence.** `loop.py` reads `next_intervention_index` before calling `ask` and
`last_hint` after it. Whether the loop calls `log_usage()` at goal end, and where
`intervention_index` and `original_step` land in the JSONL, are item 11 and 12
questions.

---

## D22 — Trajectory records are typed; all model usage is an `llm_call` record

**Decision (research lead).** The trajectory is JSONL with four record types - `step`,
`llm_call`, `verification`, `intervention` - discriminated by a single `type` field.
Every record carries `type`, `run_id` and `step`. All model usage is recorded as
`llm_call` records carrying a `role` (`policy`, `verifier`, and whatever comes later),
rather than as usage columns on the step record.

**Why.** Two rejected alternatives, recorded so they are not rebuilt:

*Dropping verifier usage* loses real data. `LLMVerifier` exposes `last_response`
precisely so the recorder can attribute verifier tokens, and at `verification_interval`
1 the verifier runs every step on a substantial prompt. Discarding it corrupts
cost-per-goal, a headline metric.

*A `verifier_*` field family* reproduces the exact failure mode D9 exists to prevent.
The schema would widen again for every new LLM caller - repair strategies at the
`execute_policy` seam (M2+), the Goal Manager (M3a/M3b), possibly history summarisation
- and every trajectory recorded before each widening would lack the new columns,
silently corrupting longitudinal comparison.

One record type with a `role` field carries all usage permanently. Cost analysis is
`sum(r["input_tokens"] for r in records if r["type"] == "llm_call")` and never changes
again.

**Consequences to preserve.**

- `verification_interval > 1` emits no verification record on a skipped step, rather
  than null columns.
- `StubVerifier` makes no API call, so it emits a `verification` record and no
  `llm_call` record. "No call made" must stay distinguishable from "call made, zero
  tokens".
- Verdict and usage live in separate records: `verification` is control-flow evidence,
  `llm_call` is cost evidence. This keeps D6's verifier/recorder separation clean.

**Explicitly not built.** No record-type registry, no polymorphic record classes, no
schema-validation layer. It is one discriminator field.

**Scope note.** No trajectories existed when this was decided, so nothing needed
migrating.
