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
