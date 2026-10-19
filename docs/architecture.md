# Architecture

Interfaces and component responsibilities. For *why* these choices were made, see
`decisions.md`. For scope and milestones, see `build-plan.md`.

## Loop shape

```
Human: goal (fixed, or open-ended "progress as far as you can")
        ↓
   ContextBuilder  ←──────────────────────┐
        ↓                                 │
   Policy LLM → Python                    │
        ↓                                 │
      FLE / Mock                          │
        ↓                                 │
   Observation                            │
     ↙       ↘                            │
 Recorder   Verifier                      │
    ↓          ↓                          │
  JSONL   done? ──yes──→ end              │
    ↓          │no                        │
 Scorer   StuckDetector                   │
(passive)      │stuck                     │
          ask_human() → guidance ─────────┘

 stop: 300 steps, or estimated spend ≥ $50 → end   (E0, D65)
 annotations/<run_id>.jsonl — hand-written after the run, never read by the loop
```

The Goal Manager (M3+) was removed with M3 (`decisions.md` D60).

## Component responsibilities

| Module | Owns | Must not |
|---|---|---|
| `goal.py` | `Goal`, `GoalResult` | Contain subtasks, decomposition, or planning graphs |
| `envs.py` | `EnvProtocol`, `MockFactorioEnv`, `RealFactorioEnv` | Let the mock interpret submitted Python |
| `rendering.py` | `render_observation(obs) -> str` | Call an LLM; know about goals |
| `context.py` | Assemble goal + observation + history + guidance + errors | Own control flow |
| `llm.py` | `LLMClient`, `LLMResponse`, policy extraction | Contain model-specific branching |
| `verifier.py` | `VerificationResult`; decide goal completion | Request help; modify state; decide stuckness |
| `stuck.py` | `StuckDetector`; decide whether to request help | Decide goal completion; modify state |
| `human.py` | `HumanProtocol`; three backends | Execute actions; touch the environment |
| `trajectory.py` | `TrajectoryRecorder`, JSONL output | Determine success |
| `loop.py` | Control flow, termination, counters | Assemble prompts; format observations |
| `run.py` | CLI, config loading | Business logic |
| `watch.py` | Keep a connected client's camera on the agent (D45) | Touch the loop, the recorder or the agent |
| `scorer.py` *(E0)* | Milestones reached, from recorded FLE state (build-plan §5d) | Drive control flow; decide goal completion; read the verifier |
| `annotations/` *(E1+)* | Catalogue rows per stuck event (build-plan §5b) | Be read by the loop; replace verbatim guidance |

## Data flow invariants

- **Verifier drives control flow. Recorder never does.** The recorder's output exists
  so verifier claims can later be checked against objective state.
- **Stuck detection is not verification.** Separate signals, separate clocks. The
  detector runs every step; verifications may be sparse when
  `verification_interval > 1`, so the consecutive-non-DONE detector counts verification
  *events*, not steps.
- **Guidance flows one way**: human → recorder (verbatim) and human → context. It never
  reaches the environment.
- **The loop terminates immediately on `done`**, with no further policy LLM call.
- **The Scorer, like the Recorder, never drives control flow.** It measures how far a
  run got from objective state; the verifier's verdict is not an input to it.
- **The budget stop reads raw tokens through the pricing table** at runtime; the
  trajectory still stores no dollar figure.

## Counters (see `decisions.md` D12)

| Counter | Reset by intervention | Effect at threshold |
|---|---|---|
| stuck counter | yes | triggers `human.ask()` |
| `interventions_without_progress` | no | aborts the goal |

## Extension points reserved for future scope

These exist as seams only. **Do not implement the features now.**

| Seam | Future use |
|---|---|
| `execute_policy(policy, env)` boundary | Repair strategies (specialized harness, E2) |
| `StuckDetector` protocol | Flat-flow, repeated-action, explicit-blocked detectors (E2) |
| `ContextBuilder` | Memory and decomposition aids (specialized harness, E2) |
| `EnvProtocol` | `RealFactorioEnv`, checkpoint/restore via `GameState` (out of scope) |
| Config file rather than constants | Experiment runner over the eval grid (E0/E3) |

*The Goal Manager seam (M3a/M3b technology selection) was dropped by D60.*
