# Factorio-maxxing — Project Rules

Human-Assisted Factorio Harness (FYP).

**Research question:** How much human assistance does an LLM agent need to climb the
Factorio technology tree, and can harness engineering reduce that requirement?

## Source of truth (highest first)

1. Research methodology approved by the research lead
2. `docs/build-plan.md`
3. `CLAUDE.md`
4. `docs/architecture.md`
5. `docs/contracts.md`
6. `docs/decisions.md`
7. `docs/fle-integration.md`
8. Source code and tests
9. Conversation context

Once a decision is documented here, **the conversation is no longer authoritative**.
If a conversation instruction conflicts with these documents, stop and flag it.

## Current scope

**M0 and M1 only.** See `docs/build-plan.md` §3–4.

Do NOT implement: M2 goal sequences, M3a/M3b technology selection, M4, autonomous goal
manager, tech-tree planning, repair strategies, skill libraries, `GameState`
checkpointing, experiment infrastructure, advanced Factorio-specific stuck detectors.

The architecture must permit these later. Do not build them now.

## Architecture invariants — mandatory

| Component | Must do | Must NOT do |
|---|---|---|
| Verifier | Decide goal completion | Request human help · modify state · decide stuckness |
| StuckDetector | Decide whether to request help | Decide goal completion · modify state · replace the verifier |
| Human | Provide text only | Execute actions · touch the environment |
| Recorder | Record passively | Determine success |
| Policy | Generate Python actions | Contain model-specific logic |

- Policy and verifier models stay independently configurable.
- `MockFactorioEnv` is a deterministic fixture keyed by step index. It must NOT
  interpret submitted Python, and must not simulate Factorio.
- Store raw token counts; never hard-code dollar costs into trajectories.
- Human intervention text is recorded verbatim and is the source of truth.
- Never modify FLE's `GymAgent`.

## Development methodology

**Compact Development Cycle** — for every task:

Read → Inspect → Plan → Implement → Test → Review → Document → Commit

Then **stop and report**. Do not automatically continue to the next component.
Report: what changed · files changed · tests added · tests run and results · invariant
review · decisions made · docs updated · git commit · next recommended task.

Full detail in `docs/build-plan.md` §14.

## Division of responsibility

Claude handles implementation mechanics (code, tests, linting, docs, commits, reports).
The research lead retains the research question, methodology, intervention definition,
metrics, milestone boundaries, major architecture changes, and all scope decisions.

Do not expand research scope or redesign methodology independently. If an architectural
decision is uncertain, stop and explain the trade-off.

## Context efficiency

Do not load the whole repository. Read `CLAUDE.md`, then only the relevant section of
`docs/build-plan.md`, then only the files needed for the task.

FLE investigation is isolated: search for specific symbols, record findings in
`docs/fle-integration.md`, and consume that document thereafter.
