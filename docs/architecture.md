# Architecture

Interfaces and component responsibilities. For *why* these choices were made, see
`decisions.md`. For scope and milestones, see `build-plan.md`.

## Loop shape

```
Human: high-level goal
        ↓
   Goal Manager  ──────────────┐   (M3+; human-supplied until then)
        ↓                      │
   ContextBuilder              │
        ↓                      │
   Policy LLM → Python         │
        ↓                      │
      FLE / Mock               │
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

## Counters (see `decisions.md` D12)

| Counter | Reset by intervention | Effect at threshold |
|---|---|---|
| stuck counter | yes | triggers `human.ask()` |
| `interventions_without_progress` | no | aborts the goal |

## Extension points reserved for future scope

These exist as seams only. **Do not implement the features now.**

| Seam | Future use |
|---|---|
| `execute_policy(policy, env)` boundary | Repair strategies (M2+) |
| `StuckDetector` protocol | Flat-flow, repeated-action, explicit-blocked detectors |
| `EnvProtocol` | `RealFactorioEnv`, checkpoint/restore via `GameState` |
| Config file rather than constants | Experiment runner sweeping model/seed/goal/backend |
| Goal Manager position in the diagram | M3a/M3b technology objective selection |
