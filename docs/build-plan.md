# Human-Assisted Factorio Harness — Implementation Plan

---

# PART I — RESEARCH CONTEXT AND SCOPE

## 1. Thesis

> **How much human guidance does a budget LLM need to reach frontier-model performance
> in Factorio, and how much of that guidance can harness engineering replace?**

*Superseded thesis (research lead, 2026-10-16, `decisions.md` D60): "How much human
assistance does an LLM agent need to climb the Factorio technology tree, and can harness
engineering reduce that requirement?"*

Human assistance is a **first-class component of the loop**, not a fallback.

The evaluation grid (§5a) splits the thesis into three measurable questions:

| | Question | Comparison |
|---|---|---|
| (a) | How large is the gap between a frontier and a budget model? | run 1 vs run 5 |
| (b) | **Does human guidance close it?** — the headline | runs 6, 7 vs run 1 |
| (c) | Can harness engineering substitute for the human? | run 8 vs runs 6 and 1 · run 4 vs run 2 |

**Why the environment is hard.** FLE's leaderboard, at 5000 autonomous steps:

| Model | Production Score | Milestones | Lab tasks | Deepest item |
|---|---|---|---|---|
| Claude 3.5 Sonnet | 293,206 | 30 (13 auto) | 21.9% | `plastic-bar` |
| Gemini 2 Flash | 115,782 | 20 (6) | 13.0% | `iron-gear-wheel` |
| GPT-4o | 87,599 | 30 (9) | 16.6% | `plastic-bar` |
| DeepSeek V3 | 48,585 | 22 (7) | 15.1% | `plastic-bar` |

The strongest model reached plastic bars. The paper attributes this to decomposition
and spatial-planning failure rather than code-generation failure — precisely the gap
human textual assistance targets. No 2026-generation model has published FLE results.

## 2. Definition — human intervention

> A human intervention occurs when the harness detects that the agent is stuck and
> requests information from a human, who provides textual guidance that is subsequently
> incorporated into the agent's context.

The human **never** plays Factorio, **never** executes actions, and provides **only**
textual knowledge. Intervention text is recorded **verbatim** and is the source of
truth. The human speaks **only when the harness asks** — better stuck detection is
therefore a property of the harness under test (D60).

Every stuck event, answered or not, receives a **post-hoc annotation** (§5b). This is
required, not optional, but it is an additional record only: it is stored apart from
the trajectory, never read by control flow, and never replaces the raw text.

## 3. Scope

**Complete:** M0, M1 (tags `m0-complete`, `m1-complete`). M1's evidence is the
goal-5 rehearsal `20261008T180209-c4fb9b83`: *Research automation* completed live, with
three harness-triggered, verbatim interventions, the recorded state confirming the
verifier.

**Current — implement now:** **E0** (§4).

**Future, in order:** E1 → E2 → E3 → E4.

**Dropped** (D60): M2 goal sequences, M3a/M3b technology selection, M4, the autonomous
goal manager, autonomous tech-tree planning. The open-ended goal and the milestone
ladder replace them as the measure of how far an agent gets.

**Allowed only from E2:** specialized-harness features — memory, skill library, repair
strategies, decomposition aids, Factorio-specific stuck detectors and the like. Each
must be a switchable feature, off in the standard harness, and should target an error
category from the catalogue (§5b). `GameState` checkpointing stays out of scope unless
the research lead admits it.

The architecture must make later phases possible. Create an interface or extension
point only where genuinely necessary; do not build a phase before its turn.

## 4. Phase ladder

| | Phase | Delivers | Done when |
|---|---|---|---|
| **M0** ✓ | Autonomous loop | Goal → policy → Python → FLE → observation → verification | — |
| **M1** ✓ | Human rescue | + stuck detection → human guidance → policy continues | — |
| **E0** | Eval scaffolding | Open-ended goal ("progress as far as you can") · objective milestone **Scorer** · **300-step cap and $50 spend ceiling** (D65) · annotation schema · one run config per grid cell | A live run stops at its cap and its milestones are scored from recorded state |
| **E1** | Standard-harness pilot | Runs 1, 2, 5, 6 at small n · first error catalogue · codebook v1 · guidance protocol in use | Every stuck event in the pilot is annotated |
| **E2** | Specialized harness | Features chosen from the E1 catalogue, each switchable | Each feature has tests and a pilot run |
| **E3** | Full grid | All 8 runs × n seeds | Every run recorded and annotated |
| **E4** | Analysis | Gap closure · catalogue write-up · cost and human-effort tables | Results tables regenerate from trajectories and annotations |

**M1 was the first meaningful research system**; E0–E4 turn it into the experiment.
The catalogue from E1 decides what the specialized harness contains, so E2's design is
driven by observed failures rather than guessed in advance.

Budgets are explicit: 300 steps per run (the binding limit), plus a $50 spend ceiling
as a safety net (D65). The project does not promise a rocket launch ahead of results.

## 5. Metrics

**Primary capability metric — highest milestone reached** on the §5d ladder, computed
by the Scorer from recorded FLE state, never from the verifier. Secondary: FLE
production score · research count · steps and spend to each milestone.

**Primary human-assistance metric — interventions**, read with: interventions
requested vs answered · characters of guidance · guidance category · human time per
answer.

**Headline — gap closure**, on highest milestone:

```
gap closure = (budget model with human − budget model alone)
            / (frontier model alone − budget model alone)
            = (run 6 or 7 − run 5) / (run 1 − run 5)
```

1.0 means the budget model with guidance matches the frontier model without it; above
1.0, it exceeds it. Undefined when run 1 ≤ run 5 — itself a reportable result.

Capability and assistance are always read as a pair: an agent needing zero
interventions but making no progress is not better than one needing several and
advancing.

**Cost.** Store raw tokens only. Dollars are derived from one pricing table — at
analysis time for reporting, and at runtime for the budget stop. No trajectory stores
a dollar figure.

**Also recorded:** steps per milestone · intervention timing · verifier verdict vs
objective FLE state · input/output/cache-read/cache-write tokens · latency · execution
error taxonomy (assertion / code / environment).

## 5a. Evaluation grid

| | Standard, no human | Standard + human | Specialized + human | Specialized, no human |
|---|---|---|---|---|
| **Frontier model** | 1 | 2 | 3 | 4 |
| **Budget model** | 5 | 6 | 7 | 8 |

With two budget models (D64) the budget row is repeated for each: runs 5-8 for Haiku 5.5
and 5'-8' for GPT 6 Luna.

- **Standard harness** = the M1 harness upgraded with the strengths of FLE's published
  agent (`GymAgent`), written in this repository rather than imported (D66): FLE-style
  instructions, FLE-level entity detail, character position / game time / score / saved
  functions, stale-entity trimming in history, and an 8-step window plus a rolling LLM
  history report - with the M1 human interface (verifier, stuck detector, human
  backends) and the admin-access guard. A baseline modelled on FLE's agent, not a
  replication of it. Supersedes D64 item 1 and D60's "M1 harness at `m1-complete`".
  **Specialized harness** = our own, built in E2 on top of the standard harness.
- **Equal steps, not equal dollars (D65).** Every run is capped at **300 steps** (one
  policy program per step; verifier, history-summary and human turns use no steps but
  their tokens are recorded), with a **$50 safety ceiling** against runaway spend. Cost is
  an outcome, not the limit: it is reported per run and as **cost to each milestone**
  (Scorer first-step x per-step cost). *Supersedes the original equal-dollar design*: with
  a ~40x price gap, equal dollars let a budget model match milestones by sheer volume of
  attempts, confounding the human's contribution with step count. Human effort is still
  reported separately (§5).
- **The verifier uses the policy's model in every run**, and its tokens count toward
  the $50 ceiling. A frontier verifier in a budget run would be frontier help.
- **Same world and starting inventory** across every run: FLE ignores a reset seed, so
  the world is the cluster's scenario map, and a "seed" is a repeat run. Every run starts
  **empty-handed**, as FLE's `open_play` does (D70).
- **Configs:** `configs/grid/cell-*.json`, one per cell - today the six standard cells
  (1, 2, 5, 6, 5', 6'); 3, 4, 7, 8 arrive with the E2 harness. Cells differ only in model,
  human backend and label; a test enforces it. Each run's trajectory opens with a `run`
  record of its full config and commit (D70).
- Further models are further rows; the grid does not change shape.
- **Models (D64):** frontier **Claude Opus 5.5**; budget **Claude Haiku 5.5** and
  **GPT 6 Luna** - two budget rows, so the grid has 12 cells per seed.
- **Seeds (D65):** Opus 5.5 runs **one seed** (4 runs, at most $200), for budget
  reasons. **TBD — research lead:** seeds for the budget models (cheap, ~$1 per run;
  ≥3 recommended).

## 5b. Error and guidance catalogue

A required output: what agents get wrong when stuck, and what human text unsticks
them. One row per stuck event in `annotations/<run_id>.jsonl`, separate from the
trajectory:

```
run_id · step · intervention_index · error_category · error_note ·
guidance_category · unstuck (yes/no, within k steps) · codebook_version
```

**Error codebook v0:** API misuse · spatial / placement · recipe / tech knowledge ·
planning / ordering · observation misread · repetition loop · verifier false DONE ·
environment / infra · other.

**Guidance codebook v0:** `KNOWLEDGE` · `DIAGNOSIS` · `PLANNING` · `CORRECTION` ·
other.

- Stuck events in no-human runs are annotated too (error fields only): they show what
  goes wrong when nobody helps.
- Labelled by hand after each run. LLM pre-labelling is allowed only if a human checks
  every label.
- The codebook is revised once, after E1, and every row carries its version.

**The codebook is a workbook: `docs/codebook-v0.xlsx`** (research lead, 2026-10-19). It
holds the definitions, inclusion and exclusion rules, and trajectory signals for each
code; an Annotations sheet to fill in, with drop-downs restricted to the codebook; worked
examples from the goal-5 rescue run, coded by Claude for the research lead to confirm;
and a Summary sheet of counts, unstuck rates and an error x guidance cross-tab. It adds two
annotation fields to the list above: secondary codes, and whether a hint mentioned API
calls (the 5c protocol check). **Decided at E0 step 22 (D69):** the workbook is where
annotating happens; `python -m factorio_maxxing.annotations export` writes its
Annotations sheet to `annotations/<run_id>.jsonl`, which analysis reads. Recorded
fields - stuck reason, intervention index, policy model, guidance text - are copied from
the trajectory, never the sheet. **`unstuck` is computed, not judged:** `yes` if the
Scorer records a milestone first reached within k steps after the event (k from the
workbook's README, 10), `n/a` if the run ends sooner, `no` otherwise. This replaces the
README's draft "or the detector does not fire again", which D67's 20-step backstop would
have made nearly always true.

## 5c. Guidance protocol

The operator is one person who learns across runs; these controls bound that bias.

- **Before E1, write down what a hint may contain** — facts, diagnosis, next sub-goal —
  and what it may not: code.
- **Alternate which model is guided first** within each seed, so operator learning is
  spread across both rows.
- **Log the time taken to answer each request.**
- **`ScriptedHuman` replay** of one run's hints against another harness is a secondary
  analysis only: the goal-5 replay did not reproduce (hint 3 landed at step 29, not 65).

## 5d. Milestone ladder — draft, research lead to confirm

Each milestone is checked by the Scorer from recorded FLE state.

1. Iron ore in inventory
2. Iron plate smelted
3. Burner mining drill `WORKING`
4. Drill → furnace: automated iron plates
5. Copper plate
6. Iron gear wheel and electronic circuit crafted
7. Steam power producing electricity
8. Lab powered and `WORKING`
9. `automation` researched
10. Assembler producing automation science packs
11. Electric mining drill `WORKING`
12. `logistics` researched
13. Logistic science pack produced
14. … extended toward `plastic-bar`, the deepest item on the FLE leaderboard

---

# PART II — ARCHITECTURE

## 6. Diagram

```
Human: goal (fixed, or open-ended "progress as far as you can")
        ↓
   ContextBuilder  ←──────────────────────┐
        ↓                                 │
   Policy LLM → Python                    │
        ↓                                 │
      FLE                                 │
        ↓                                 │
   Observation                            │
     ↙       ↘                            │
 Recorder   Verifier                      │
    ↓          ↓                          │
  JSONL   done? ──yes──→ end              │
    ↓          │no                        │
 Scorer   StuckDetector                   │
(offline       │stuck                     │
 or passive) ask_human() → guidance ──────┘

 stop: 300 steps, or estimated spend ≥ $50 → end   (E0, D65)
 annotations/<run_id>.jsonl — written by hand after the run, never read by the loop
```

The Goal Manager box of the original diagram (M3+) is removed with M3 (D60).

## 7. Architecture invariants — mandatory

| Component | Must do | Must NOT do |
|---|---|---|
| **Verifier** | Determine whether the goal is complete | Request human assistance · modify state · determine stuckness |
| **StuckDetector** | Determine whether to request human assistance | Determine goal completion · modify Factorio state · replace the verifier |
| **Human** | Provide text only | Execute actions · touch the environment |
| **Recorder** | Record passively | Determine success |
| **Policy** | Generate Python actions | Contain model-specific logic |
| **Scorer** *(E0)* | Compute milestones reached from recorded FLE state | Drive control flow · decide goal completion · read the verifier's verdict |

Policy and verifier models must remain independently configurable. In eval runs both
are set to the same model (§5a); configurability is kept for development.

## 8. Key design decisions

**Drive the gym environment directly; do not subclass `AgentABC`.** `AgentABC` is
shaped for FLE's own trajectory runner, which owns the loop. The harness owning the
loop is central to the research system. Keep the prompt format reasonably close to
FLE's `GymAgent` for later baseline comparison. **Never modify `GymAgent`.**

**Model ladder: DeepSeek V3 → Haiku 4.5 → Sonnet 5 → Opus 5.** DeepSeek V3 is the
low-cost API baseline. **Develop on Haiku 4.5, not the cheapest model** — a 32-step
goal costs ~$0.58 on Haiku, so a hundred development runs is ~$58 against ~$6 on
DeepSeek. That ~$50 saving is not worth the ambiguity: during M0/M1 every failure must
be attributable primarily to harness or infrastructure problems, not weak model
capability. No model-specific logic enters the core harness.

**Superseded for the experiment by D60: a frontier row and budget rows.** The eval
grid (§5a) runs **Claude Opus 5.5** as the frontier model and **Claude Haiku 5.5** and
**GPT 6 Luna** as budget models (D64), with further models as further rows. The ladder above still describes development:
build and debug on Haiku 4.5 so that failures stay attributable to the harness.

**Model access is unrestricted.** `APIFactory.PROVIDERS` is only a `base_url` +
key-env pair per provider, routed through a generic `AsyncOpenAI` client; model strings
pass through with no allowlist. Use OpenRouter (`open-router-` prefix) for cheap
cross-provider exploration on one key; use direct provider APIs at volume (OpenRouter
marks up ~2–4×).

**Verifier and recorder are separate.** The verifier is LLM-based, receives goal +
latest observation + production flows + a short observation/history window, returns
DONE/NOT-DONE + reason, and drives control flow. The recorder is passive, records
objective FLE state, and never determines success. The window matters: sustained-
production goals cannot be judged from one snapshot — FLE's own lab tasks verify by
sleeping 60s and measuring throughput for exactly this reason. Separation preserves the
ability to compare *"verifier said DONE"* against *"FLE state shows success"*.

**Stuck detection is pluggable and is not goal completion.** Initial detectors:
N consecutive non-DONE verifications, and repeated execution/error signature.
**Consecutive non-DONE must not be the sole signal** — an agent can make legitimate
progress while the goal remains incomplete, and a detector firing on that interrupts a
healthy build. Future detectors (flat production flow, repeated identical actions,
explicit blocked signal, Factorio-specific indicators) are ablation candidates; do not
implement them now.

**Three human backends, one interface.** `NoHuman` (autonomous baseline, no
assistance), `InteractiveHuman` (CLI, development and real runs), `ScriptedHuman`
(replays recorded interventions). `ScriptedHuman` is a **methodological safeguard**:
replaying the same intervention set against harness A and harness B lets improvement be
attributed to the harness rather than to a better human hint. Without `ScriptedHuman`
and `NoHuman`, unattended multi-seed runs are impossible.

**`ScriptedHuman` matches by sequence order, never by step index.** The Nth stuck
event receives the Nth recorded hint. Step-keyed matching fails silently in exactly the
case the safeguard exists for: if harness A got stuck at steps 7/15/22 and harness B
gets stuck at 9/20, step-keyed replay hands B nothing. Record both
`intervention_index` (used for matching) and `original_step` (for analysing *when* help
was needed). Log when the hint list is exhausted or left underused — "harness B used 2
of harness A's 3 hints" is itself a result. On exhaustion, degrade to `NoHuman`.

## 8a. Intervention lifecycle

**A successful intervention resets the stuck counter**, because the agent now has new
information and deserves a fresh window to act on it. Without the reset the counter
remains above threshold and the detector re-fires on the next step, producing an
intervention storm.

**A second counter, `interventions_without_progress`, is not reset**, and aborts the
goal when it exceeds its threshold. This is the "human help isn't working" state, and
it is what allows `InteractiveHuman` runs to terminate sensibly instead of burning
`max_steps` and the operator's patience on an unsolvable goal.

**Guidance accumulates and persists until the goal ends.** It is a list, not a single
slot: a single slot lets a later hint silently erase an earlier one that is still true.
Guidance renders as a labelled `HUMAN GUIDANCE` context section, most recent last.
Interventions are rare by design and M0/M1 goals are ≤32 steps, so token cost is
negligible; compaction is a later concern and must not be pre-optimised.

**The detector runs every step, and treats verifications as a sparse signal.** Three
of its four inputs — history, flows, errors — update every step; only verifications are
sparse. It therefore reads verifications as "most recent known verdict", not "verdict
for this step". Consequently the consecutive-non-DONE detector counts **verification
events, not steps**: at `verification_interval: 4` with `stuck_threshold: 3` it would
not fire for 12 steps, which is why the error-signature detector exists as the fast
path. The two detectors run on different clocks. This is inert at M0/M1 where the
interval is 1, and is specified now to prevent a subtle bug when sparse verification is
first tested.

**Cost instrumentation is first-class.** Store raw usage; never hard-code dollars.
Cost is derived at analysis time from a single pricing table so trajectories stay
re-priceable when pricing changes.

**Offline-first.** `MockFactorioEnv` is a **fixture, not a simulator**. It is scripted
by step index and deliberately does not interpret submitted Python — parsing arbitrary
code to decide transitions would mean building a fake Factorio. It exists to test loop
control, parsing, rendering, verification plumbing, stuck detection, human backends,
trajectory recording, and replay. Real Factorio-specific behaviour belongs in FLE.

**Blocking constraint.** Neither WSL2 nor Docker is installed. `wsl --install` needs
admin rights and a reboot — the user's step. Build-order steps 1–14 need none of it.

## 9. Repository structure

```
CLAUDE.md
pyproject.toml
docs/
    build-plan.md          # this document, moved into the repo
    architecture.md
    contracts.md
    decisions.md
    fle-integration.md
factorio_maxxing/
    __init__.py
    goal.py
    envs.py
    rendering.py
    context.py             # see note
    llm.py
    verifier.py
    stuck.py
    human.py
    trajectory.py
    loop.py
    run.py
    watch.py               # live-watching camera, a side channel (D45)
    scorer.py              # E0: milestones from recorded state (§5d)
tests/
    test_package.py  test_goal.py  test_envs.py  test_rendering.py
    test_context.py  test_llm.py  test_llm_parsing.py  test_api_client.py
    test_verifier.py  test_stuck.py  test_human.py  test_trajectory.py
    test_loop.py  test_run.py
    test_loop_offline.py  test_replay.py  test_watch.py
    fixtures/
configs/harness.example.json
experiments/configs/       # E0: one config per grid cell (§5a)
trajectories/
annotations/               # E1+: catalogue rows per run (§5b), hand-written
```

> **Note — one deviation from the specified file list.** `context.py` is added because
> context assembly (goal + observation + history + guidance + execution errors) must
> live somewhere, and placing it in `loop.py` couples loop control to prompt
> construction — which the spec explicitly warns against, and which is exactly the axis
> later experiments will vary (compact vs full observations, history compression).
> It stays a small module with one job. Recorded in `docs/decisions.md`.

## 10. Contracts

```python
# goal.py — minimal. No subtasks, decomposition, or planning graphs.
@dataclass(frozen=True)
class Goal:
    description: str
    max_steps: int = 32
    notes: str | None = None

@dataclass
class GoalResult:
    goal: Goal
    completed: bool
    steps_used: int
    interventions: int
    reason: str
    trajectory_path: Path

# envs.py
class EnvProtocol(Protocol):
    def reset(self) -> dict: ...
    def step(self, action: Action) -> tuple[dict, float, bool, bool, dict]: ...

# llm.py
@dataclass
class LLMResponse:
    text: str; model: str
    input_tokens: int; output_tokens: int
    cache_read_tokens: int; cache_write_tokens: int
    latency_seconds: float

# verifier.py
@dataclass
class VerificationResult:
    done: bool
    reason: str

# stuck.py — decides when to ask for help, never whether the goal is complete
class StuckDetector(Protocol):
    def is_stuck(self, history, verifications, flows, errors) -> tuple[bool, str]: ...

# human.py
class HumanProtocol(Protocol):
    def ask(self, goal: Goal, observation: str, reason: str) -> str | None: ...
```

**Rendering.** `render_observation(obs) -> str` emits compact sections — `INVENTORY`,
`ENTITIES`, `RESEARCH`, `FLOWS`, `EXECUTION` - since D66 also `CHARACTER`, `FUNCTIONS`
and `GAME`, with every entity in FLE-level detail (contracts.md). Never send raw FLE
observations to the policy model. Rendering stays separate from the LLM client so
compact/full/compressed variants can be tested without touching environment or LLM
interfaces.

**History window: 8 steps, plus a running history report** (D66). FLE's
`RecursiveReportFormatter(chunk_size=16)` counts *messages* - 8 steps - and folds older
steps into an LLM-written report; this section originally read "16 steps", mistaking
messages for steps and dropping the report. History entries omit entity lists; only the
current observation carries them.

**Trajectory JSONL records.** Superseded: the flat field list originally given here was
replaced by four typed record types - `step`, `llm_call`, `verification`,
`intervention` - discriminated by a single `type` field, with all model usage recorded
as `llm_call` records carrying a `role`. Cost analysis is then one sum over `llm_call`
records however many model callers the harness grows, rather than a schema widening per
caller. The schema is in `contracts.md`; the reasoning, including the two rejected
alternatives, is in `decisions.md` D22.

## 11. Core loop

```python
obs = env.reset()
for step in range(goal.max_steps):
    rendered = render_observation(obs)
    prompt   = context.build(goal, rendered, history, guidance, errors)
    resp     = policy_llm.generate(prompt)
    policy   = extract_policy(resp.text)
    obs, reward, *_ = env.step(Action(code=policy))
    recorder.record(step, goal, policy, obs, resp, reward)

    if step % verification_interval == 0:
        result = verifier.check(goal, obs, flows_window)
        recorder.record_verification(step, result)
        if result.done:
            return GoalResult(completed=True, steps_used=step + 1, ...)

    stuck, why = detector.is_stuck(history, verifications, flows, errors)
    if stuck:
        guidance = human.ask(goal, rendered, why)
        recorder.record_intervention(step, why, guidance)

    history.append((policy, rendered))
return GoalResult(completed=False, ...)
```

Default `verification_interval: 1`. The loop must terminate **immediately** on
`result.done` with **no further policy LLM call**.

`execute_policy(policy, env)` wraps parse → validate → execute → observe. No sandbox;
make stdout, stderr and exceptions observable and feed them back. Since D66 it refuses a
program that reaches for admin access (`instance`, RCON, console commands, dunder
introspection) without submitting it. This boundary is
where repair strategies attach in future scope.

## 12. Config

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
  "history_length": 8,
  "history_report_interval": 8,
  "environment": "mock",
  "trajectory_dir": "trajectories",
  "api_reference": ""
}
```

Later decisions added keys; `configs/harness.example.json` carries every one, and
contracts.md describes them. Eval runs set `max_steps` to 300 (D65).

API keys come from environment variables, never this file. An identity-linked API key
also needs `ANTHROPIC_WORKSPACE_ID` set alongside it (`decisions.md` D27).

`api_reference` is a path to a file describing the functions the environment provides,
rendered to the policy as an `ENVIRONMENT API` section. Empty means the policy is told
nothing about the API. Added after this section was first written, in response to the
first live run, where the policy model invented an API because the prompt asked for
Python without saying which functions exist (`decisions.md` D28).

**That file now exists**: `configs/fle_api_reference.md`, generated by FLE's own
`SystemPromptGenerator` and carrying the same ~29,200-token surface the published
baseline sends (`decisions.md` D31). The default above stays empty because a relative
path default breaks any run started outside the repository root;
`configs/harness.example.json` points at it, and a live run must too. Note the cost: at
`verification_interval` 1 this adds ~29k input tokens to **every** policy step, which
supersedes the per-goal cost estimate in §8.

---

# PART III — ENGINEERING WORKFLOW

## 13. Persistent project knowledge and source-of-truth hierarchy

Files are the source of truth, not conversation context.

**Authority order — highest first:**

1. Research methodology approved by the research lead
2. `docs/build-plan.md`
3. `CLAUDE.md`
4. `docs/architecture.md`
5. `docs/contracts.md`
6. `docs/decisions.md`
7. `docs/fle-integration.md`
8. Source code and tests
9. Conversation context

Once a decision is documented in the repository, **the conversation is no longer
authoritative**. If a conversation instruction conflicts with repository documentation,
stop and flag the conflict rather than silently guessing which wins.

| File | Contains |
|---|---|
| `CLAUDE.md` | Short. Project-wide rules and invariants only. |
| `docs/build-plan.md` | This document. |
| `docs/architecture.md` | Architecture decisions and interfaces. |
| `docs/contracts.md` | Python contracts. |
| `docs/decisions.md` | ADR log — decisions and *why*. |
| `docs/fle-integration.md` | FLE findings, so future sessions don't rediscover them. |

**Seed `docs/decisions.md` with decisions already settled**, so the reasoning behind
them is not lost: drive gym directly rather than subclass `AgentABC`; separate policy
and verifier models; mock-as-fixture; recorder/verifier separation; develop on Haiku
rather than the cheapest model; store raw tokens rather than computed cost; three human
backends; `context.py` deviation.

## 14. The Compact Development Cycle

**This is the single development methodology for the project.** It replaces and
consolidates all other development-cycle instructions; no other section defines a
competing workflow.

For every implementation task:

| # | Step | Meaning |
|---|---|---|
| 1 | **Read** | Read only the relevant project documentation and contracts. |
| 2 | **Inspect** | Inspect existing code, tests, and dependencies relevant to the task. |
| 3 | **Plan** | State the smallest coherent change **before** coding. |
| 4 | **Implement** | Implement one coherent component or task only. |
| 5 | **Test** | Run targeted tests, then the appropriate broader suite. |
| 6 | **Review** | Check architecture, invariants (§7), edge cases, scope creep, unnecessary complexity. |
| 7 | **Document** | Update documentation and `docs/decisions.md` when something meaningful changed. |
| 8 | **Commit** | One small, descriptive Git commit. |

### Stop-and-report contract

After completing one cycle:

> **Stop and report. Do not automatically continue to the next major component.**

The report must contain: what changed · files changed · tests added · tests run and
results · architectural/invariant review · decisions made · documentation updated · git
commit · next recommended task.

For non-trivial components, steps 3, 4 and 6 are run as distinct passes: a **planning
pass** that inspects and proposes without modifying code; an **implementation pass**
covering exactly the approved scope; and a **QA pass** reviewing against `CLAUDE.md`,
this plan, contracts, tests, and §7 — looking specifically for unnecessary complexity,
scope creep, incorrect interfaces, missing tests, state leakage, off-by-one errors,
incorrect loop termination, and violations of recorder/verifier separation.

## 15. Division of responsibility

| Claude — implementation mechanics | Research lead — retains control |
|---|---|
| Inspecting code | Research question |
| Creating and editing files | Experimental methodology |
| Writing tests | Human-intervention definition |
| Running tests, formatting, linting | Metrics |
| Reviewing implementation | Milestone boundaries |
| Updating documentation | Major architecture changes |
| Creating Git commits | Research assumptions |
| Reporting results | Scope changes |
| | Whether a deviation from the plan is acceptable |

**Claude must not independently expand research scope or redesign the methodology.**
Where an architectural decision is uncertain, stop and explain the trade-off rather than
choosing unilaterally. Where a conversation instruction conflicts with repository
documentation, stop and flag the conflict rather than guessing.

## 16. Context efficiency

Do not repeatedly load or summarise the whole repository. Before a task: read
`CLAUDE.md` → the relevant section of `docs/build-plan.md` → relevant contracts → only
the source files needed.

**FLE investigation is isolated.** Search for specific symbols (`make_factorio_env`,
`Action`, `Observation`, `GymAgent`, `list_available_environments`) rather than dumping
subsystems into context. Record findings in `docs/fle-integration.md`; subsequent work
consumes that document instead of rediscovering the API.

When context grows large, summarise completed work into project documentation and start
a fresh context rather than continuing indefinitely. After a coherent task completes,
the source of truth is git + code + tests + docs + fixtures — not the conversation.

## 17. Git discipline

Small commits:

```
feat: add Goal contracts
feat: add deterministic mock environment
feat: add observation renderer
feat: add context builder
feat: add LLM abstraction and stub
feat: add policy parser
feat: add verifier
feat: add stuck detector
feat: add human backends
feat: add trajectory recorder
feat: add M0/M1 loop
test: add offline regression suite
feat: add CLI
```

Branch or tag before major experimental changes. Tag milestones `m0-complete` and
`m1-complete`. Do not rewrite experimental history.

## 18. Experiment infrastructure — built in E0 and E3

*Originally "do not build yet". Brought into scope by D60: the evaluation grid (§5a)
is the experiment, so the runner is built in E0 (one config per cell, budget stop,
Scorer) and exercised at scale in E3. The parameter list below gains `harness`
(standard / specialized) and `max_spend_usd` (D65).*

Architecture must eventually support `model · seed · goal · human backend · stuck
detector · verification interval · history length` as configurable parameters, so that
an experiment runner can later do:

```bash
python -m factorio_maxxing.experiment \
    --config experiments/configs/haiku_m1.json --seeds 1 2 3 4 5
```

recording `run_id · model · seed · goal · completed · steps · interventions · tokens ·
latency · errors · trajectory`. **Not implemented until M1 works.** The only obligation
now is that no current design choice forecloses it — which is why config is a file, not
hard-coded constants.

---

# PART IV — BUILD ORDER AND DONE CRITERIA

## 18a. Machine handoff

Development moves to a second machine (more RAM) after Phase 0. Repo:
`https://github.com/skai8468/Factorio-maxxing.git`, `main` tracking `origin/main`.
Working copy is `C:\Users\leong\dev\Factorio-maxxing`; the former OneDrive path is an
empty shell to be deleted.

Claude Code sessions are **local to the machine they run on** — conversation history
does not transfer. Everything needed on the second machine must therefore be in the
repository before the switch. **Phase 0 is the handoff mechanism**: it moves this plan
from conversation context (authority rank 9) into `docs/build-plan.md` (rank 2), where a
fresh session can read it.

Phase 0 must be committed and pushed before development moves.

## 19. Build order

**Phase 0** — repo scaffolding: `CLAUDE.md`, `docs/` seeded, this plan copied to
`docs/build-plan.md`, `decisions.md` seeded with the settled decisions, committed and
pushed.

**Phase 1** — 1. `pyproject.toml` · 2. `goal.py` · 3. `envs.py` mock · 4. `rendering.py`

**Phase 2** — 5. `llm.py` + stub · 6. policy parsing · 7. `context.py` · 8.
`verifier.py` · 9. `stuck.py` · 10. `human.py`

**Phase 3** — 11. `trajectory.py` · 12. `loop.py` · 13. offline tests · 14. `run.py` CLI

**Phase 4** — 15. real LLM + mock environment

**Phase 5** — 16. *user installs WSL2 + Docker + FLE* · 17. `RealFactorioEnv` ·
18. first live goal

Phases 0–4 need no API key, no Docker, no WSL, no Factorio. **Do not skip to live
Factorio before the offline loop works.**

Phases 0–5 are complete. The evaluation build order (D60), each step one Compact
Development Cycle:

**E0** — 18b. FLE pinned to 0.4.8 @ `e2a829d`, patched by `scripts/patch_fle.sh` (D61) ✓ ·
18c. standard harness upgraded with FLE's strengths - instructions, entity detail,
observation fields, stale-entity trimming, history report - plus the admin-access guard
(D66) ✓ · 19. `scorer.py`: milestones from a recorded trajectory, tested on recorded
fixtures (D62) ✓ · 20. open-ended goal mode: no verifier, no intervention abort,
repeated errors plus a 20-step backstop (D67) ✓ · 21. 300-step cap plus a `max_spend_usd` ceiling ($50, D65) computed from raw tokens
and the pricing table (D68) ✓ · 22. annotation schema and a validator, exported from the
codebook workbook (D69) ✓ · 23. one config per grid cell, and a `run` record in every
trajectory (D70) ✓ ·
24. a live run that stops at its cap and is scored

**E1** — 25. guidance protocol written · 26. pilot of runs 1, 2, 5, 6 · 27. annotate
every stuck event · 28. codebook v1

**E2** — 29+. specialized-harness features, one per cycle, each switchable and each
naming the catalogue category it targets

**E3** — full grid, n seeds · **E4** — analysis

## 20. Definition of done

**M0 complete** when `Goal → Policy → Python → Mock/FLE → Observation → Verifier →
DONE` works reliably and is tested.

**M1 complete** when `Goal → Policy → Python → FLE → Observation → Verifier →
StuckDetector → Human → Guidance → Policy` works reliably, and every intervention is
harness-triggered, textual, recorded verbatim, and fed back into subsequent policy
context — with the human never executing Factorio actions.

Both are met (tags `m0-complete`, `m1-complete`; evidence in §3).

**E0 complete** when a live run in any grid-cell config stops at its step cap or spend
ceiling (D65), its
milestones are scored from recorded state by a tested Scorer, and an annotation file
for it validates against the schema.

**E1 complete** when runs 1, 2, 5 and 6 have been piloted under the written guidance
protocol and every stuck event is annotated under codebook v1.

**E2 complete** when each specialized-harness feature is switchable, tested, piloted,
and tied to the catalogue category it targets.

**E3 complete** when all eight runs × n seeds are recorded and annotated.

**E4 complete** when the results tables — gap closure, milestones, interventions,
cost, human effort, catalogue frequencies — regenerate from trajectories and
annotations alone.

---

# PART V — VERIFICATION

## 21. Test coverage required before a milestone is declared complete

| Area | Cases |
|---|---|
| Goal | construction · defaults · validation |
| Rendering | expected sections · compact formatting · missing/empty fields |
| Context | assembles goal, observation, history, guidance, errors; respects window |
| Policy parsing | fenced Python · bare Python · malformed responses |
| Mock env | deterministic transitions · reset · step |
| Verifier | DONE · NOT DONE · reasons |
| Stuck detector | fires at threshold · **does not fire during legitimate incomplete progress** · repeated error detection |
| Human | NoHuman · InteractiveHuman · ScriptedHuman · deterministic replay · **sequence-order matching when stuck steps differ** · exhausted hint list degrades to NoHuman |
| Intervention lifecycle | stuck counter resets on intervention · **no re-fire on the next step** · `interventions_without_progress` does *not* reset and aborts the goal at threshold · guidance accumulates across the goal · earlier guidance survives a later intervention |
| Trajectory | token counts · latency · verification · verbatim intervention text · execution errors |
| Loop | normal completion · failure after max steps · stuck → human → continue · NoHuman · ScriptedHuman · verification interval · **immediate termination on DONE with no extra LLM call** |

```bash
pytest
```

must pass before any milestone is declared complete.

## 22. Offline smoke run

```bash
python -m factorio_maxxing.run --goal "Build a working iron mining setup" \
  --mock --policy-model stub --verifier-model stub --human none
```

## 23. Live setup

```bash
wsl --install          # admin, requires reboot
```

Then Docker Engine, installed from Docker's official signed apt repository **inside the
WSL2 distro**, with `systemd=true` in `/etc/wsl.conf` so `dockerd` starts at boot. This
supersedes the earlier "Docker Desktop with the WSL2 backend": FLE is Linux-first, and
running the engine in the distro keeps containers, the `fle` package and the harness in
one network namespace instead of routing RCON across a second distro
(`decisions.md` D30). The working copy **stays on Windows** at
`C:\Users\leong\dev\Factorio-maxxing` and is reached from WSL as
`/mnt/c/Users/leong/dev/Factorio-maxxing`; the virtualenv carrying FLE is created on the
Linux filesystem, never on `/mnt/c`. This supersedes the earlier instruction to move the
repo to `~/projects/factorio-maxxing`: the OneDrive objection lapsed at the machine
handoff (§18a), the `/mnt/c` speed objection is answered by siting the virtualenv on the
Linux side, and moving the repo would move the Claude Code sessions with it
(`decisions.md` D29). Then `uv sync`; `fle cluster start`; confirm
`list_available_environments()` returns task keys.

## 24. Live smoke test — harness and environment only, not the experiment

```bash
python -m factorio_maxxing.run \
  --goal "Place a burner mining drill on iron ore, fuel it, and verify it is working" \
  --live --policy-model claude-haiku-4-5 --verifier-model claude-haiku-4-5 \
  --human interactive
```

Passes when the loop terminates as completed **and** the recorded trajectory
independently shows an entity with `status == WORKING` — the passive recorder
confirming what the verifier claimed.

## 25. M0/M1 goal progression — complete, superseded by §5d

*Kept as the record of how M0/M1 were tested. For the experiment it is replaced by the
milestone ladder (§5d), which the open-ended goal climbs in a single run (D60).*

1. Place and operate a burner mining drill
2. Produce iron plates
3. Build a working furnace-based iron production setup
4. Produce the resources needed for automation
5. **Research automation** — *M1 stress test*

Step 5 is not a smooth increment. Automation requires 10 automation science packs —
copper plates *and* iron gears *and* an assembler producing science *and* a lab, several
FLE lab tasks' worth of work. It is included deliberately as the point where M0 is
expected to fail and M1's human assistance becomes necessary. Failure there without
intervention is the expected result, not a defect.

## 26. FLE components to reuse

| Need | Use | Path |
|---|---|---|
| Env creation | `make_factorio_env` | `fle/env/gym_env/registry.py` |
| Action format | `Action(code, agent_idx, game_state)` | `fle/env/gym_env/action.py` |
| Observation schema | `.research`, `.flows`, `.entities`, `.inventory` | `fle/env/gym_env/observation.py` |
| Baseline comparison | `GymAgent` — read, never modify | `fle/agents/gym_agent.py` |
| Model routing | `APIFactory` | `fle/agents/llm/api_factory.py` |
| Checkpointing *(future)* | `GameState.from_instance()` | `fle/commons/models/game_state.py` |
| Research control *(future)* | `set_research`, `get_research_progress` | `fle/env/tools/agent/` |
