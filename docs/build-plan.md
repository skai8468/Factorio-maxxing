# Human-Assisted Factorio Harness — Implementation Plan

---

# PART I — RESEARCH CONTEXT AND SCOPE

## 1. Thesis

> **How much human assistance does an LLM agent need to climb the Factorio technology
> tree, and can harness engineering reduce that requirement?**

Human assistance is a **first-class component of the loop**, not a fallback.

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
truth. Optional later classification (`KNOWLEDGE` / `DIAGNOSIS` / `PLANNING` /
`CORRECTION`) is an additional field only — it must never affect M0/M1 control flow or
replace the raw text.

## 3. Scope

**Current — implement now:** M0, M1, offline mock, real FLE integration, human
intervention machinery, policy/verifier model separation, trajectory and cost
instrumentation.

**Future — do not implement:** M2, M3a, M3b, M4, autonomous goal manager, autonomous
tech-tree planning, repair strategies, skill library, `GameState` checkpointing,
evaluation/ablation framework, advanced Factorio-specific stuck detectors.

The architecture must make these possible later. Create an interface or extension point
only where genuinely necessary; do not prematurely build the feature.

## 4. Milestone ladder

| | Milestone | Question |
|---|---|---|
| **M0** | Goal → policy → Python → FLE → observation → verification | Can the harness execute a goal autonomously? |
| **M1** | + stuck detection → human guidance → policy continues | Can a human rescue an otherwise-stuck agent without controlling the game? |
| M2 | Goal sequences | Can it execute a sequence of goals? |
| M3a | Human-selected technology objectives | Can it execute a human-chosen tech objective? |
| M3b | Harness selects next objective | Can it choose its own objective? |
| M4 | Full system | See below |

**M1 is the first meaningful research system**, not merely a fallback feature. M0 alone
approximates existing work; M1 is where the project's question becomes measurable.

**M3b is the major difficulty jump** — autonomous technology selection reintroduces the
long-horizon planning that the leaderboard shows models fail at. M3a de-risks it by
separating *executing* an objective from *choosing* one.

**M4, concretely** — not "play Factorio":
> Given a fresh Factorio world and a high-level instruction to climb the technology
> tree, the harness repeatedly achieves increasingly difficult technology objectives,
> requests human textual assistance when stuck, incorporates that guidance, and records
> progression until a configured endpoint or budget is reached.

Budgets are explicit: deepest technology, max steps, max interventions, max API spend.
The project does not promise a rocket launch ahead of results.

## 5. Metrics

**Primary capability metric — tech-tree depth** (deepest technology reached). Becomes
measurable at M3a; at M0/M1 its stand-in is **goal success rate**, since current scope
does not touch the tech tree.

**Primary human-assistance metric — interventions required.**

**Normalised — interventions per technology milestone** (per successful goal at M0/M1).

**Goal success rate is reported separately per human backend.** The delta between
`NoHuman` and `InteractiveHuman` over the same goal set *is* the M1 result — e.g.
*"M0 solves 2/5 goals autonomously; with human assistance the harness solves 4/5,
requiring 6 interventions."* Both backends must therefore be run over an identical goal
set for the comparison to mean anything.

Intervention count alone is insufficient: an agent needing zero interventions but making
no progress is not better than one needing several and advancing. Capability and
assistance metrics are always read as a pair.

**Also recorded:** goals succeeded/failed · steps per goal · intervention frequency over
time · intervention classification · verifier verdict vs objective FLE state ·
input/output/cache-read/cache-write tokens · latency · execution error taxonomy
(assertion / code / environment) · cost derived later from raw token counts.

---

# PART II — ARCHITECTURE

## 6. Diagram

```
Human: high-level goal
        ↓
   Goal Manager  ──────────────┐   (M3+; human-supplied until then)
        ↓                      │
   ContextBuilder              │
        ↓                      │
   Policy LLM → Python         │
        ↓                      │
      FLE                      │
        ↓                      │
   Observation                 │
     ↙       ↘                 │
 Recorder   Verifier           │
    ↓          ↓               │
  JSONL   done? ──yes──────────┘
             │no
        StuckDetector
             │stuck
        ask_human()
             ↓
      guidance → ContextBuilder
```

## 7. Architecture invariants — mandatory

| Component | Must do | Must NOT do |
|---|---|---|
| **Verifier** | Determine whether the goal is complete | Request human assistance · modify state · determine stuckness |
| **StuckDetector** | Determine whether to request human assistance | Determine goal completion · modify Factorio state · replace the verifier |
| **Human** | Provide text only | Execute actions · touch the environment |
| **Recorder** | Record passively | Determine success |
| **Policy** | Generate Python actions | Contain model-specific logic |

Policy and verifier models must remain independently configurable.

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
tests/
    test_goal.py  test_rendering.py  test_context.py  test_llm_parsing.py
    test_verifier.py  test_stuck.py  test_human.py
    test_loop_offline.py  test_replay.py
    fixtures/
configs/harness.example.json
experiments/configs/       # empty until M1 complete
trajectories/
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
`ENTITIES` (position + status), `RESEARCH`, `FLOWS`, `EXECUTION`. Never send raw FLE
observations to the policy model. Rendering stays separate from the LLM client so
compact/full/compressed variants can be tested without touching environment or LLM
interfaces.

**History window: 16 steps**, matching FLE's `RecursiveReportFormatter(chunk_size=16)`
for baseline comparability. No complex memory system.

**Trajectory JSONL fields:** `run_id · step · goal · policy · observation · reward ·
model · input_tokens · output_tokens · cache_read_tokens · cache_write_tokens ·
latency_seconds · verification · stuck_reason · human_intervention · execution_errors`.

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
make stdout, stderr and exceptions observable and feed them back. This boundary is
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
  "history_length": 16,
  "environment": "mock",
  "trajectory_dir": "trajectories"
}
```

API keys come from environment variables, never this file.

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

## 18. Future experiment infrastructure — do not build yet

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

## 20. Definition of done

**M0 complete** when `Goal → Policy → Python → Mock/FLE → Observation → Verifier →
DONE` works reliably and is tested.

**M1 complete** when `Goal → Policy → Python → FLE → Observation → Verifier →
StuckDetector → Human → Guidance → Policy` works reliably, and every intervention is
harness-triggered, textual, recorded verbatim, and fed back into subsequent policy
context — with the human never executing Factorio actions.

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

Then Docker Desktop with the WSL2 backend; move the repo out of OneDrive to
`~/projects/factorio-maxxing` (OneDrive fights venvs and Docker volumes; `/mnt/c` paths
are slow); `uv sync`; `fle cluster start`; confirm `list_available_environments()`
returns task keys.

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

## 25. M0/M1 goal progression

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
