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

---

## D23 — Loop control flow: declined help is recorded, and the intervention cap is hard

**Decision (research lead approved).**

1. A request for help is recorded as an `intervention` record whether or not it is
   answered. A declined request carries `text: null` and `intervention_index: null`.
   Interventions are counted as records whose `text` is not null. **The stuck window
   resets on any request, answered or declined** (amended during item 13; see below).
2. `interventions` does not reset and aborts the goal on reaching `max_interventions`
   (default 3) - at most that many interventions per goal.
3. The loop does not call `ScriptedHuman.log_usage()`; `run.py` does it at end of run,
   keeping the loop backend-agnostic.
4. History pairs a policy with the observation that *followed* it, and is appended
   before stuck detection rather than at the end of the iteration.
5. Execution errors are read from the `error` and `stderr` observation keys.
6. The prompt's EXECUTION ERRORS section shows only the most recent step's errors.

**Why.**

Recording declined requests preserves the baseline half of the M1 comparison. Under
`NoHuman` the detector still fires; if nothing were recorded, the trajectory would show
no evidence the harness ever detected stuckness, and "how often would help have been
requested" - the question `NoHuman` exists to answer - would be unanswerable from the
data. Counting only answered requests keeps `NoHuman` at zero interventions.

Build-plan section 8a says `interventions_without_progress` "is not reset", and no
progress signal is defined at M0/M1, so the counter is a hard cap on interventions per
goal. The name anticipates a progress signal that current scope does not have; should
one arrive, this is where it attaches.

Section 11's pseudocode appends the *pre-step* rendering to history, which would show
each policy beside the state before it ran. `context.py` labels these "step N policy"
and "step N result", and the result is the informative half. Appending before stuck
detection also keeps history, verifications, errors and flows advancing on the same
clock, so one set of window marks resets all four (D20).

**Consequence.** `_record_verifier_usage` reads `last_response` by attribute rather
than by type, so any verifier that makes a model call gets its usage recorded and one
that makes none writes no `llm_call` record (D22). This is backend-agnostic, not
model-specific branching.

---

## D24 — The stuck window resets on any request for help, not only an answered one

**Decision (research lead).** Requesting help advances the stuck window marks whether or
not the human answers. Intervention *counting* is unchanged: a declined request is still
zero interventions.

**Why.** Found by an end-to-end test at item 13. Under `NoHuman` nothing is ever
answered, so under the original reading - reset only on a successful intervention - the
window never advanced and the detector fired on every step past the threshold. That is
the intervention storm build-plan section 8a exists to prevent, arriving through the
declined path instead of the answered one; the storm is caused by the detector re-firing
on the next step, which happens identically either way.

It also broke the baseline. Over the same goal, `InteractiveHuman` recorded requests at
steps 1 and 3 while `NoHuman` recorded 1, 2 and 3, so "how often would help have been
requested" could not be read across backends - which is the question `NoHuman` exists to
answer, and half of the M1 result (D8, D13).

**Consequence.** Request counts are comparable across human backends over an identical
goal set. `interventions` still counts answered requests only, so `NoHuman` reports zero
interventions while its stuck events remain visible in the trajectory.

---

## D25 — CLI: strict config, refusals that name their phase, demo fixtures in `run.py`

**Decision.** `run.py` merges defaults, then a config file, then command-line overrides.
Unknown config keys are rejected rather than ignored. A non-`stub` model or
`--live` is refused with an error naming the build-order item that will provide it
(Phase 4 item 15, Phase 5 item 17). The mock environment's frames, the stub policy
responses and the stub verdicts used by the offline smoke run live in `run.py` as
`DEMO_*` constants. `run.py` calls `ScriptedHuman.log_usage()` at end of run (D23).

**Why.** Silently ignoring `max_stpes` would run 32 steps while the operator believed
they had asked for 8, and the trajectory would record the run as configured correctly -
a data-integrity problem, not a usability one. Refusals name their phase so an operator
who tries `--live` learns when it arrives rather than that it is broken.

The `DEMO_*` fixtures are the smoke run's script. They belong to the CLI rather than to
`envs.py`, which must stay a general fixture holder, and they are explicitly a
demonstration: three scripted frames in which a drill is placed, fuelled and starts
working. They exercise the loop and simulate nothing (D10).

**Consequence.** A failed goal exits 0. Failure is a legitimate research result -
build-plan section 25 expects goal 5 to fail without assistance - so only a
configuration or I/O error exits non-zero (2).

---

## D26 — One generic API client; routing is a table, not an allowlist

**Decision.** `APIClient` is a single OpenAI-compatible client. `PROVIDERS` maps a
provider to a base URL and a key environment variable; `MODEL_ROUTES` maps a model
prefix to a provider, stripping `open-router-` and `ollama-` before the request. An
unroutable model is a routing failure that `provider=` overrides, never a rejected
model. `openai` is an optional `api` extra, imported lazily. `temperature` is omitted
from the request unless explicitly set. Usage fields absent from a provider's response
record as zero.

**Why.** This is FLE's `APIFactory` shape, and it keeps D5 intact: the capability ladder
stays a config sweep rather than a code change, and nothing in the harness branches on
which model is in use. Routing by prefix is a lookup table, so a model released
tomorrow works today - there is a test asserting that an unreleased `claude-*` string
passes through untouched.

`openai` stays optional so Phases 1-3 install with zero dependencies and the offline
suite runs on a machine that will never make an API call. Lazy import means importing
`llm.py` never requires it.

Omitting `temperature` follows the note in `fle-integration.md` that reasoning models
may reject it. Recording absent usage fields as zero keeps a provider that reports no
cache tokens from killing a run mid-goal.

**Unverified.** Cache-token field names (`prompt_tokens_details.cached_tokens`,
`cache_creation_input_tokens`) are read defensively and are unconfirmed against each
provider's live response. Confirm per provider on the first live call and tighten then.

**Consequence.** `run.py` no longer refuses non-stub models; a missing key fails with
the name of the environment variable to set. The environment stays mock: this item is
the model path only, and live Factorio remains Phase 5 item 17.

---

## D27 — Identity-linked API keys: the workspace id travels as a header

**Decision.** `APIClient` sends `anthropic-workspace-id` whenever a workspace id is
configured, read from `ANTHROPIC_WORKSPACE_ID` or passed explicitly. The header is sent
for every provider rather than only for Anthropic.

**Why.** Found on the first live call. An identity-linked API key - the per-user
credential type, as opposed to a workspace-scoped key - is rejected with a 400 unless
the request names the workspace it acts in. Auth itself was fine; only the workspace was
missing.

Sending it for every provider keeps account shape out of the routing table: a provider
that does not use the header ignores it, whereas branching on provider here would put a
credential detail into `MODEL_ROUTES` and give the harness its first provider
conditional (D5).

**Consequence.** Both `ANTHROPIC_API_KEY` and, for an identity-linked key,
`ANTHROPIC_WORKSPACE_ID` must be in the environment. Neither is ever read from a config
file.

---

## D28 - The environment API is supplied to the policy, not hard-coded

**Decision.** `context.build` takes an `api_reference` string and renders it as an
`ENVIRONMENT API` section, first in the prompt. `run.py` reads it from a file named by
the new `api_reference` config key. Empty is allowed and renders nothing. The file's
contents are not authored yet.

**Why.** The first live call showed a policy model inventing an API - `game.scan(...)`,
`game.insert(...)`, `game.get_inventory()` - because the prompt asked for Python without
saying which functions exist. FLE's `GymAgent` puts its API surface in the prompt; ours
did not.

Supplied rather than hard-coded because the authority on that surface is the
environment, not this module: a reference written from memory would be a fabrication
carrying the harness's authority, and every resulting failure would read as model
incapability rather than a harness defect. It renders first because it is the most
stable content across a goal, which is also the right position should prompt caching be
used later.

**Consequence.** `api_reference` is a twelfth config key; build-plan section 12 lists
eleven and predates this. The M0/M1 offline work is unaffected - the mock ignores
submitted code (D10) - but a live run before the reference exists would fail on every
policy. `docs/fle-integration.md` records what must be captured from the installed
source and why it must not be written from memory.

---

## D29 - The working copy stays on Windows; the Linux virtualenv lives in WSL

**Decision (research lead).** The canonical Git working copy remains at
`C:\Users\leong\dev\Factorio-maxxing`, reached from WSL2 as
`/mnt/c/Users/leong/dev/Factorio-maxxing`. The virtualenv carrying FLE is created on the
Linux filesystem inside WSL, never on `/mnt/c`. Build-plan section 23's instruction to
move the repo to `~/projects/factorio-maxxing` is superseded, and that section is updated
to match. A second working copy inside WSL, synchronised by git, is explicitly rejected.

**Why.** Section 23 gave two reasons to move: OneDrive fights venvs and Docker volumes,
and `/mnt/c` paths are slow. The first no longer applies - the working copy left OneDrive
at the machine handoff (section 18a). The second applies to dependency trees and Docker
volume mounts rather than to source files, and creating the FLE virtualenv on the Linux
filesystem answers it directly. This package is a handful of small pure-Python modules
with no build step, so reading them across the 9P mount costs nothing that matters.

Against that, moving the repo into WSL moves the Claude Code sessions with it, and
sessions are local to the machine and path they run on (section 18a). Two working copies
were rejected outright: divergence between them would be silent, which is intolerable in
a project whose sessions deliberately carry no context and treat the repository as the
source of truth.

**Consequence.** `fle cluster start` and every FLE call run inside WSL2; git, editing and
the offline suite continue to run on Windows against the same files. One checkout now
backs two Python environments - the Windows `.venv` of phases 1-4, and the WSL virtualenv
that carries FLE. `RealFactorioEnv` (item 17) must therefore import FLE lazily, so that
`pytest` on Windows, where FLE is absent, still collects and passes. D14 already makes FLE
an optional extra; this is the reason that choice has to hold.

---

## D30 - Docker Engine inside WSL2, not Docker Desktop

**Decision (research lead).** Docker Engine is installed from Docker's official signed
apt repository inside the Ubuntu-24.04 WSL2 distro, with `systemd=true` in `/etc/wsl.conf`
so `dockerd` starts at boot. Docker Desktop is not installed. Build-plan section 23's
"Docker Desktop with the WSL2 backend" is superseded and that section is updated.

**Why.** FLE is Linux-first - `fle/cluster/run-envs.sh` is bash and cluster startup
assumes Linux Docker networking. Engine-in-distro puts the containers, the `fle` package
and our harness in one Linux network namespace, so the RCON path from Python to a
Factorio container is plain loopback with nothing crossing a distro or OS boundary.
Docker Desktop would have run the engine in its own utility distro, adding a hop that
exists only to be debugged later.

Secondary: no Windows-side background service on a laptop, no elevated install, and no
Docker Desktop licensing question to revisit if this work is ever published or run
somewhere with a company behind it.

**Cost accepted.** No dashboard - container inspection is `docker ps` / `docker logs`.
This was weighed against having fewer moving parts in the network path and judged worth
it, on the same reasoning as D3: when a live run fails, the cause must be attributable,
and each additional hop is a place a failure can hide.

**Consequence.** Docker is unavailable from Windows; anything touching containers runs
inside WSL. `systemd=true` is now load-bearing rather than cosmetic - without it
`dockerd` does not start and every FLE call fails at connection time.

---

## D31 - The API reference is generated by FLE, committed, and sent in full

**Decision (research lead).** `configs/fle_api_reference.md` is generated by FLE's own
`SystemPromptGenerator.generate()` and committed to this repository. It carries the
**full** reference - types, entities, method schema and all 28 `agent.md` manuals,
116,827 chars / ~29,200 tokens. `configs/harness.example.json` points `api_reference` at
it; the `Config` default stays `""`. FLE's MIT notice ships beside it as
`configs/FLE_LICENSE`.

**Why generated, not written.** D28 settled that the authority on the callable surface is
the environment. Generating from `SystemPromptGenerator` is the strongest form of that:
it is the identical call `fle/eval/entrypoints/gym_eval.py:150` makes to build the
published baseline's system prompt, so the content cannot drift from what FLE actually
provides, and no sentence of it was written from memory.

**Why full.** The baseline sends all of it as a system message on every call. Sending
less would mean any M0/M1 result carried a caveat that our policy saw less API
documentation than the published numbers did - and a policy that fails from a thin
reference produces exactly the attribution ambiguity D3 exists to prevent. The 19k-token
manual is where usage protocol lives (`move_to` before `place_entity`, and similar), not
just prose.

**Cost, stated plainly.** ~29,200 input tokens per policy step. At
`verification_interval` 1 over a 32-step goal that is roughly 930k tokens of API text per
goal, and it **invalidates the ~$0.58-per-goal figure** in D3 and build-plan section 8.
FLE requests no prompt caching and neither do we - `llm.py` reads
`cache_read_tokens`/`cache_write_tokens` but never sets `cache_control`. Enabling caching
is an open item, deliberately not decided here. Thinner variants remain available as
experiment arms because D28 made this a file path (build-plan section 18).

**Why the `Config` default stays empty.** A default would have to be a relative path, and
`load_api_reference` exits 2 on a missing file, so every run from outside the repository
root would fail. The example config carries the path instead, which is why
`test_the_example_config_matches_the_config_fields` no longer asserts example == defaults;
two narrower tests replace it, one of which now guards that the reference file exists and
still has its `types`/`methods`/manual structure.

**Committed rather than generated at setup**, because the prompt is the experiment: a
trajectory is only comparable to another if the API text that produced it is pinned. FLE
upgrades must therefore regenerate it deliberately, from `~/venvs/fle`:

```python
from pathlib import Path
import fle.env
from fle.env.utils.controller_loader.system_prompt_generator import SystemPromptGenerator
body = SystemPromptGenerator(str(Path(fle.env.__file__).parent)).generate()
# keep the provenance comment at the top of the existing file, then replace the body
```

**Unconfirmed.** That this actually stops the model inventing calls. The failure it
targets was observed; the fix has not yet been demonstrated against live Factorio
(item 18).

---

## D32 - `RealFactorioEnv` is a thin adapter with a lazy import

**Decision.** `envs.py` gains `RealFactorioEnv`, satisfying the existing `EnvProtocol`
with no change to it. FLE is imported inside `__init__`, never at module scope.
Construction goes through `get_environment_info(task_key)` ->
`GymEnvironmentSpec(**info)` -> `make_factorio_env(spec, run_idx)`, not `gym.make`.
`reset()` unpacks FLE's `(observation, info)` pair and returns the observation alone;
`step()` translates our `Action` into FLE's. `enable_vision` defaults off. A thirteenth
config key, `task_key`, defaults to `open_play`.

**Why the lazy import.** The working copy is on Windows and FLE lives in the WSL
virtualenv (D29), so the offline suite runs where FLE is absent. A module-scope import
would make `envs.py` - which every other module imports - unimportable there, taking the
whole suite with it. The lazy import is what lets 314 tests pass on a machine that cannot
install FLE, and it is exercised by a test asserting no module-level `fle` import exists.

**Why not `gym.make`.** FLE registers each task with `gym.register`, so `gym.make` looks
like the intended path, but it wraps the environment in `OrderEnforcing` and
`PassiveEnvChecker`. FLE's `reset()` does not follow the Gymnasium contract closely
enough to survive the checker. `make_factorio_env` returns the environment unwrapped.

**Why `step` translates rather than duck-types.** FLE's `step` opens with
`assert isinstance(action, Action)` against its own class, so a structurally identical
object is rejected. `game_state` is left `None`: checkpointing is future scope (D15).

**Why `task_key` is a config key and `open_play` the default.** `open_play` is used as a
**neutral sandbox**, not as a task whose success criteria we adopt - the `Goal` drives the
policy and our own verifier decides completion (D6). Overloading `environment` to carry
the task name was rejected as ambiguous.

**Verified live**, against the running container: the adapter constructs, satisfies
`EnvProtocol`, `reset()` returns a dict, `step()` executes submitted Python for real
(`harvest_resource` returned 5 coal) and returns the five-tuple, and `close()` releases
the instance.

**Consequence, and it is not small.** Driving a real observation through
`render_observation` showed the renderer emitting `INVENTORY (none)` while the agent held
5 coal. The observation's value shapes are lists of typed dicts, not the mappings
`rendering.py` assumes (table in `fle-integration.md`). The adapter is correct and the
renderer is not; a live goal is pointless until that is fixed, because the policy cannot
see its own inventory. That is the next task, not this one. Done in D33.

---

## D33 - The renderer reads live observation shapes, not fixture shapes

**Decision.** `rendering.py` is widened to the shapes a live FLE observation actually
carries, completing the tightening D16 deferred to Phase 5. No section, header or
ordering changed - only the parsing beneath them - so prompt structure is unaffected and
prior trajectories stay comparable.

Six changes, each traced to a measured shape:

1. `_as_counts` accepts `type` and `item` as label keys beside `name`, and `quantity`
   and `rate` as value keys beside `count` and `amount`. One observation uses three
   different quantity keys: `quantity` in inventory, `rate` in flows, `amount` in
   harvested.
2. `technologies` is counted whether it arrives as a list or a dict. FLE's dataclass
   says dict; the gym observation delivers a list.
3. `current_research` arriving as the **literal string `"None"`** is treated as absent.
   The observation space is typed, so idle research serialises to a truthy string.
4. A numeric `research_progress` renders as `progress: N`, and only while a research is
   actually active. Previously an idle `0` rendered as `remaining: 0`, which reads as a
   research that finished rather than one that never started.
5. `flows.crafted` records craft *events* - `{crafted_count, inputs, outputs}` - so its
   outputs are flattened. Inputs are deliberately not shown: the policy needs what it now
   has, not what was consumed, and the same items already appear under `input`.
6. `Position` objects and enum members render as `(63, -52)` and `NO_FUEL`. A live
   observation carries real Python objects, not JSON.

**Why this was invisible.** Every rendering test used `{name: count}` mappings, so the
suite was green while the renderer was blind. The tests added here transcribe measured
live observations, and one asserts that the mapping and list shapes render *identically*,
so fixtures and reality cannot drift apart again.

**Why the fallback stayed.** An unrecognised flow shape still renders as `N items` rather
than vanishing. A wrong count is a visible defect; a silently dropped section is the
failure that just cost a live debugging session.

**Verified live** against the container: inventory, entities with position, direction and
status, research totals, and all four flow categories all render correctly.

**Not changed.** The recorder still stores observations verbatim, numpy scalars and all
(D6, and the research lead's decision to keep `research` unabridged). Rendering is where
the policy's view is shaped; recording is where objective state is preserved. This
change touches only the former.

---

## D34 - The cluster runs Factorio 2.0.77, not FLE's pinned 2.0.73

**Decision (research lead).** The Factorio server image is moved from
`factoriotools/factorio:2.0.73` to `2.0.77`, by patching the three pins in the installed
FLE package. Backups are kept as `.orig` beside each file, and the change is documented in
`fle-integration.md` because a reinstall silently reverts it.

**Why.** Watching a run requires a game client, and Factorio multiplayer demands an
**exact** version match. Steam offers 2.0.77, 2.0.76 and 2.0.72 - but not 2.0.73 - so the
client cannot be matched to FLE's pin. One side had to move.

**Why the server rather than the client.** FLE's README states **"version 2.0.73 or
later"**, so 2.0.77 sits inside its stated support; the installed package contains no
runtime version check and ships no mods beyond `base`, so nothing is compiled or validated
against a specific build. The alternative - sourcing a 2.0.73 client outside Steam - would
have left the machine's game install diverging from its store, for no gain.

**Risk accepted, and checked rather than assumed.** A version bump could break FLE's Lua,
which is loaded into the running game. The live checks were re-run on 2.0.77: all ~48 Lua
tools load, `harvest_resource` and `craft_item` execute, the observation shapes are
unchanged, and `render_observation` produces byte-identical output to 2.0.73. If a later
failure looks Factorio-specific, this is the first thing to suspect and `.orig` is the way
back.

**Consequence.** The substrate differs from FLE's default by four patch releases. No
results existed when this was decided, so nothing needed re-running, but any comparison
against published leaderboard numbers should note it. Build-plan section 23's live setup
now depends on a manual post-install patch - recorded in `fle-integration.md` rather than
automated, since build-plan section 9 fixes the repository structure and this is a
property of the environment, not of the harness.

---

## D35 - Execution errors are read from FLE's `info`, not from the observation

**Decision.** `execution_errors` takes the step's `info` dict beside the observation and
reads FLE's `error_occurred` flag and `result` text first, falling back to the
observation's `raw_text` when the flag is set but the text is missing. The observation
keys `error` and `stderr` are kept for the mock. `error_signature` gains two
normalisations for what FLE actually sends: it unescapes literal `\n` sequences before
splitting into lines, and strips the leading `N: ` execution-trace prefix from the line
it keeps. `loop.py` stops discarding `info`.

**Why.** D16 and D32 both deferred one question to Phase 5: which key a live FLE
observation populates for an execution error. Measured against the running container,
the answer is **none of them**. FLE reports failure in the `info` dict -
`environment.py` computes `error_occurred = "error" in result.lower() or "exception: "
in result.lower()` and returns it beside `result` - and puts the same text in the
observation's `raw_text`, where it is indistinguishable from a successful step's output.
`execution_errors` therefore returned `[]` for **every** failing live step.

**What that cost, and it is an M1 defect rather than a cosmetic one.** The
`error_signature` half of the default detector reads one error per step and fires when
three consecutive steps carry the same signature. Fed an empty list every step, it could
never fire against real Factorio - so the fast path that D7 added precisely because
consecutive non-DONE is too slow was dead live, leaving M1 dependent on the slow
detector alone. The trajectory's `execution_errors` field was likewise empty on failing
steps, so the section 5 execution-error taxonomy would have recorded nothing. The policy
was **not** blind: `raw_text` reaches it through the renderer's EXECUTION section, which
is why nothing looked wrong.

**Why the two signature normalisations are not tidying.** FLE's `parse_result_into_str`
prefixes every output line with the submitted code's line number, and embeds the
exception as a repr in which newlines survive as the two characters `\n`. The whole
failure therefore arrives as a single line that `splitlines` cannot split, so the
signature became the entire string, line number included - and the same mistake made
after an added `print` would have hashed differently, breaking the repeat detection the
detector exists for. Both are properties of FLE's formatting, so both are normalised
where signatures are computed rather than at the boundary.

**Why `info` rather than pattern-matching `raw_text`.** FLE already decides what counts
as an error and publishes the verdict. Re-deriving it from the text would duplicate a
rule we do not own and would misclassify a successful step whose output happens to
contain the word "error".

**Verified live** against the running container: the same failure submitted three times,
once behind a `print` so its line number differed, produced one identical signature on
all three and `default_detector(3)` fired. Before the change the same sequence yielded
three empty errors and no detection.

**Not changed.** The recorder still stores the observation verbatim; `info` is read for
the error field only, not recorded wholesale. The verifier is untouched - errors inform
stuckness, never completion (D6, D7).

---

## D36 - The game pause between steps is a knob, defaulted to FLE's behaviour

**Decision.** `RealFactorioEnv` takes `pause_after_action`, defaulting to `True`, and a
config key of the same name carries it; `--no-pause` turns it off for one run.
`configs/live-watchable.json` pairs it with `stuck_threshold: 8` and
`max_interventions_without_progress: 5`.

**Why.** FLE freezes the game tick after every step - `pause()` sends
`/sc game.tick_paused = true` - so no world time passes while the policy and verifier
are called. That is correct for measured runs: it makes a trajectory independent of how
long the model took to answer. It also makes a run impossible to watch. A Factorio client
connected to a server whose tick has stopped reports "server is not responding" and
eventually drops, which is what happened on the first live run: the pause held for the
whole of an intervention, because the harness was blocked waiting for a human to type.

**Why a knob rather than a change of default.** Turning the pause off means the world
moves while the agent thinks, so the observation the policy reasoned about is slightly
stale when its code lands. That is a real cost to comparability and it should never be
paid by accident. Off is for demonstrations; measured runs keep FLE's default, and the
default in code is FLE's so that omitting the key changes nothing.

**Why it is set after construction.** `make_factorio_env` builds
`FactorioGymEnv(instance=, task=, enable_vision=)` and forwards nothing else, so there is
no constructor route to the flag short of reimplementing the factory. FLE reads the
attribute once per step, so assignment is sufficient. A test asserts the flag lands on
FLE's environment and not merely on ours, since ours is not what reads it.

**The thresholds are config, not code.** With `verification_interval: 1` and
`stuck_threshold: 3`, the non-DONE detector fires every third step, and since D24 resets
the window on every request, it keeps firing every third step. With
`max_interventions_without_progress: 3` that caps a run at nine steps regardless of
`max_steps: 32` - observed live, where the run aborted at step 8. Raising the two numbers
buys a run room without touching the architecture.

**Known and not addressed here.** `ConsecutiveNonDoneDetector` cannot distinguish
legitimate incomplete progress from a stuck agent: it counts NOT DONE verdicts and reads
neither `history` nor `flows`, though both are passed to it. `flows` is the obvious
progress signal and is currently threaded through every detector and read by none. A
progress-aware detector is on the do-not-build list (CLAUDE.md, "advanced
Factorio-specific stuck detectors") and what counts as stuck is the research lead's, so
this is recorded rather than fixed. It matters for the metric: if the harness asks every
`stuck_threshold` steps by construction, the absolute intervention count measures the
config. The `NoHuman` vs `InteractiveHuman` delta is unaffected, since both run the same
configuration.

---

## D37 - FLE's error truncation is patched out, because it makes errors unreadable

**Decision (research lead).** The six sites where FLE reduces an exception message to
everything after its last colon are patched to keep the whole message. `.orig` backups
sit beside each file. The procedure is recorded in `fle-integration.md` rather than
committed as a script: build-plan section 9 fixes the repository structure, and like the
version pins this is a property of the environment, not of the harness (D34).

**Why.** Measured on the first live run. FLE's Lua raises

```
No burner-mining-drill in inventory. Current inventory: empty
```

and `tool.py::get_error_message` does `response.split(":")[-1]`, so what reaches the
policy is

```
Could not place burner-mining-drill at (-15.5, -50.5), empty
```

The agent read `empty` as "that tile is empty" and spent eight steps and two human
interventions hunting for better ground, when it simply had no drill. Step 7 is the
proof: the same failure rendered as `..., coal=20` after it had harvested coal - the
tail is the inventory listing, not a description of the terrain.

**Six sites, one pattern.** `env/tools/tool.py` holds the shared
`get_error_message`; `harvest_resource`, `get_resource_patch`, `insert_item`,
`set_entity_recipe` and `place_entity` each repeat the expression inline. Every one is
building error text - none is parsing - so removing `.split(":")[-1]` is safe at all six.

**Why this is worth a dependency patch.** It is the research question in miniature. The
agent did not fail at Factorio; it failed because the harness handed it a destroyed
error message. Whether restoring the message reduces the intervention count is directly
measurable: same goal, same model, truncated versus full, count interventions. That is a
harness-engineering result rather than a model result, which is what the thesis is about.

**Risk.** The full message is longer, so prompts grow slightly. The quote-stripping that
follows the truncation is left alone; it is cosmetic, and a minimal patch is easier to
re-apply.

**These edits live in `site-packages` and a reinstall discards them**, exactly as with
the version pins (D34). Re-run the script after any `uv pip install` that touches FLE.

---

## D38 - Watching a run means rendering it, not spectating it

**Decision.** `enable_vision` becomes a config key and a `--vision` flag, off by
default, and `trajectory.py` gains `extract_map_images`, which writes each step's
render out as `step-NNNN.png`. Video assembly is one `ffmpeg` command, documented in
`fle-integration.md` rather than wrapped in code, so the harness keeps its empty
runtime dependency list.

**Why not a game client.** Watching a live FLE run through Factorio's own client fights
the environment on two fronts, both measured. FLE freezes the tick between steps (D36),
so a connected client reports the server as unresponsive and eventually drops. And
`create_agent_characters` **destroys every character entity on the surface** before
creating the agent's, so a spectator who joined before `reset()` is deleted by it; the
agent's character is then a free-standing entity in `storage.agent_characters`, with no
player attached, which does not appear in the player list and walks off to wherever the
ore is. Both are surmountable - the pause is now a flag, and a spectator can teleport to
the entity - but the result is a camera that has to be chased by hand.

**What upstream does.** The FLE team's own `claude-code-plays-factorio` exposes
`render(x, y)` and `fle://render/{x}/{y}` and documents no live-spectate path at all.
The real-time Factorio spectating that circulates publicly (Ryan Madden's writeup, a
Nintendo Switch joining over the LAN) drives a **plain headless server over RCON**, not
FLE, so it meets neither the pause nor the character handling. Rendering is the
supported path, not a workaround.

**Why off by default.** The image is never shown to the policy, so it buys a run
nothing. It costs render time per step and puts a base64 PNG in every recorded
observation, which the recorder stores verbatim (D6). A 32-step run with vision on is a
much larger trajectory, and trajectories are the analysis artefact.

**A measurement to distrust.** `fle-integration.md` records vision costing 41,455 ->
53,903 bytes of observation JSON. That was taken with **sprites not installed**, which
FLE warns about at startup and which makes it render blank images - so the recorded
figure is the cost of nothing. It is corrected once a run with real sprites exists.

**Not decided here.** Whether the image should stay inline in the trajectory or move to
a sidecar directory. Inline keeps the recorder honestly passive and the trajectory
self-contained; a sidecar keeps trajectories small enough to read. That trade-off is the
research lead's, and nothing here forecloses it.

---

## D39 - The pause is cleared at construction, because FLE's own flag lies

**Decision.** `RealFactorioEnv.__init__` forces the game unpaused after construction,
by setting FLE's `_is_paused` attribute to `True` and then calling `unpause()`. This
runs on every construction, not only when `pause_after_action` is off.

**Why.** FLE keeps pause state in a Python attribute:

```python
self._is_paused = False        # instance.py:51, at construction, never queried
def unpause(self):
    if self._is_paused:        # instance.py:88, returns early otherwise
```

The attribute is initialised to `False` whatever the game is actually doing, and
nothing reconciles it. A run that ends with `pause_after_action` leaves
`game.tick_paused = true` set in the *game*; the next process constructs a fresh
instance whose flag says "running", so `set_speed_and_unpause` at each step start
silently sends nothing and the pause survives.

**Measured, and it is total.** The first run under `live-watchable.json` inherited a
pause from the run before it. FLE's tick counter read 420 at step 3 and 420 at step 12 -
seventeen seconds of game time across fourteen steps. Every `move_to` failed with
`Could not get path to (x, y): Path request timed out after 10 attempts`, because path
requests resolve over game ticks and there were none. The character never left `(2, 2)`,
so every placement then failed as "too far away". The agent spent the whole run
reasoning about positioning while the world was frozen underneath it.

**Why unconditionally.** A paused game is inherited the same way whatever this run
intends afterwards, and `pause_after_action=True` does not save a run: the same stale
flag defeats the unpause at step start. Turning our pause off stops us *adding* a pause;
it does nothing about one already there. Reconciling at construction makes every run
start from a known state, which is what a trajectory needs in order to be comparable.

**Why not patch FLE.** This one is reachable from outside - the attribute is public
enough to set - so the harness absorbs it rather than adding a seventh site to the list
that a reinstall silently reverts (D37). Touching a private attribute is recorded in a
docstring and covered by a test that mirrors the real guard, so an upstream fix that
makes this redundant will not break anything.

---

## D40 - Starting inventory is an explicit scenario parameter

**Decision (research lead).** `RealFactorioEnv` takes `starting_inventory`, carried by a
config key of the same name and empty by default. It is applied after `reset()`, via
FLE's per-namespace `_set_inventory`, and the environment then re-observes so the
policy's first prompt reflects it. `configs/live-watchable.json` sets
`{"burner-mining-drill": 3}`.

**Why.** `open_play` starts the agent with nothing, which quietly changes what a goal
measures. "Place a burner mining drill on iron ore, fuel it, and verify it is working"
reads as four actions, but with an empty inventory it is really: mine stone, craft a
stone furnace, mine iron ore, mine coal, smelt plates, craft gear wheels, craft the
drill, and only then place, fuel and verify. Both live runs to date died at "place" for
that reason - the agent never had a drill, and every placement error it saw was FLE's
mangled way of saying so (D37).

**Why this rather than a lab task.** FLE's throughput tasks already carry
`LAB_PLAY_POPULATED_STARTING_INVENTORY` - 500 coal, 50 drills, and much else - so
switching `task_key` would have cost no code. Two reasons not to. Those tasks run their
own `sleep`-based verification every step, and they set `terminated` on *their* success
criteria, which this loop reports as `environment terminated` - a successful run would
be recorded as a failure. And adopting a task's success criteria is exactly what D32
declined to do: `open_play` is a neutral sandbox and our verifier decides completion.

**Why three drills, and no coal.** The goal names finding coal as a step, so handing the
agent 500 coal would delete part of what is being demonstrated - it can mine its own, and
already did so unprompted on the first run. Three drills rather than one is slack for a
misplacement, not generosity.

**What it costs.** A goal run with a stocked inventory is not comparable with the same
goal run empty-handed, so the parameter belongs in any reported result alongside the
model and the goal text. It is a scenario parameter, not a hint: it is fixed before the
run and is not an intervention, so it does not touch the M1 intervention count.

**Why after reset, and why re-observe.** FLE's `reset()` runs the task's setup, which
installs the task's own starting inventory; anything set earlier is discarded. And the
observation `reset()` returns is built before the stocking, so without re-observing the
first prompt would tell the agent it owns nothing - sending it off to craft what it is
already holding. A changed FLE shape raises rather than falling back, because silence
here means an empty-handed agent, which is the failure being fixed.

---

## D41 - The renderer cannot show a factory, so it is not a way to watch a run

**Finding, measured 2026-09-11.** FLE's renderer draws terrain, ore, trees, cliffs and
alert icons. It does **not** draw entities. The sprite dataset it downloads,
`Noddybear/fle_images`, contains no entity graphics at all:

| Prefix | Count |
|---|---|
| `tree*` | 886 |
| `icon_*` (inventory icons) | 342 |
| ore / water / cliff tiles | ~350 |
| `alert-*` | the remainder |

`icon_burner-mining-drill.png` is the inventory icon, not the building.

**Proved directly.** A drill was placed next to the character and the scene rendered at
four parameter settings, down to `radius=8` where a 2x2 entity spans roughly 64 pixels.
All four show a red out-of-fuel alert triangle at the drill's position and nothing
underneath it. No parameter changes this; the sprites are not there to draw.

**Which explains the blank frames of the first successful run.** By its final step the
drill was fuelled, so there was no alert, so nothing was drawn - the frames looked like
empty landscape because, as far as the renderer is concerned, they were.

**Consequence.** `enable_vision` remains useful for the agent's own spatial reasoning,
which is what upstream built it for, and it stays available. It is not a route to a video
of an agent building a factory, and `configs/live-watchable.json` therefore turns it back
off: a render costs time on every step and a large base64 blob in every recorded
observation (D38), for images that show nothing the trajectory does not already say.

**What this leaves.** A real game client is the only way to see entities, and that path
is blocked on Docker's UDP publishing rather than on anything in this harness: a
datagram sent from Windows to port 34197 arrives in WSL when the container is stopped and
a plain listener holds the port, but with the container running the Factorio server logs
no join attempts at all. The Hyper-V firewall rule, the WSL address and mirrored
networking were each eliminated by test before this was found. Not pursued further today.

**Corrects D38**, which recommended rendering as the supported way to watch a run. The
FLE team's own `render(x, y)` is for the agent, not for an audience. That reading was
wrong and this supersedes it.

---

## D42 - A game client cannot watch a live run: joining crashes the server

**Finding, measured 2026-09-11, twice.** Connecting a Factorio client to the FLE server
during a run kills the server outright:

```
Cannot serialize entity: LuaEntity is no longer valid (entity may have been destroyed)
Saving scenario failed: The scenario level caused a non-recoverable error.
Error while running event level::on_save()
Quitting: multiplayer error.
```

Docker then restarts the container, and the harness run dies with it.

**The mechanism.** A joining client must be served the map, so the server saves it -
`changing state from(InGame) to(InGameSavingMap)`, immediately after the connection
request. FLE's scenario serialises entities in `on_save`, and it holds references that
are no longer valid once `create_agent_characters` has destroyed and recreated
characters, which `reset()` does at the start of every run. The save throws, and
Factorio treats a failed multiplayer save as fatal.

**Timing does not dodge it.** The obvious fix - join *after* the reset, when characters
are fresh - was tried and produced the identical crash at the identical place. The stale
reference is not a transient of the reset.

**Which corrects `fle-integration.md`.** "Watching a run with a real Factorio client -
verified 2026-09-07" was verified against a world **no run had ever touched**, where the
scenario's stored entity tables were still valid. That is the only condition under which
a client can join. It is not a way to watch a run.

**Everything else on that path was eliminated first**, and is worth keeping: the
handshake works end to end through Docker's UDP publishing (captured: 14 -> 26 -> 50 ->
131 bytes both ways), mirrored WSL networking makes `localhost:34197` work and removes
the shifting VM address, the Hyper-V rule is correct, and `/promote` over RCON gives the
admin rights `/c` needs. None of that was the problem.

**What would be needed.** A patch to FLE's scenario Lua so `on_save` skips invalid
entities rather than raising - a third site-packages patch, on Lua loaded into the
running game, and riskier than the two already carried (D34, D37). Not attempted.

**Consequence for the project.** There is currently no way to watch or film an FLE run
as gameplay. The renderer cannot draw entities (D41) and a client cannot connect (this
entry). What a run can produce is the terminal and the trajectory, which is what
demonstrations should be built from until one of the two is fixed.
