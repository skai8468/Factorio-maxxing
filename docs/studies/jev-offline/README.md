# Offline study: JEV as a filter for backstop help requests

**Date:** 2026-11-17 · **Script:** `scripts/jev_offline_study.py` · **Raw answers:**
`results.jsonl` · **Full output:** `analysis.txt`

**Status:** exploratory, ahead of E2. Nothing in the harness changed. Before any JEV feature
is built, the research lead decides whether one goes in (CLAUDE.md: specialized-harness
features wait for E2, each switchable and targeting a catalogued error category).

## Question

When the 20-step backstop asks for help, could JEV (TypeSafe's decision model,
`typesafe/jev-1.13`, served as `typesafe/jev-1.13-20260917` via OpenRouter's Decisions
endpoint) tell a false alarm (E10, "no error") from a real problem, so that the human
is asked only about the real ones?

## Method

- **Labels:** every annotated help request: 36 from the three Haiku cell-5 runs
  (`3e77267c`, `ba5dcf0a`, `2cd7aa8c`). 24 E10, 12 real (4 E08, 4 E04, 2 E02, 1 E01,
  1 E03). 21 codes are verified by the research lead; the 15 from `2cd7aa8c` are drafts.
  All 36 came from the backstop: none came from the repeated-error detector.
- **State given to JEV:** only what a detector could see at that step. That is the last 5
  programs, each with its output and errors (truncated), and a 20-step summary taken from
  the recorded observation: steps with errors; entities and working entities;
  technologies researched; current research; inventory size; production score. It gets
  no Scorer milestones (the Scorer may not drive control flow) and nothing after the
  request.
- **Questions** (true/false, a probability each):
  - `making_progress`
  - `stuck`
  - `environment_fault`

  The wording is in the script.
- **Repeats:** 3 calls per request, 108 calls in total, scored on the mean.

## Results

| Filter rule: skip the human when... | E10 filtered | Real problems wrongly filtered |
|---|---|---|
| `making_progress >= 0.8` | 22/24 | 7/12 |
| `making_progress >= 0.9` | 14/24 | 1/12 (E02) |
| **`making_progress >= 0.9` and `stuck <= 0.1`** | **10/24** | **0/12** |
| `stuck <= 0.1` | 16/24 | 2/12 (E03, E02, both draft codes) |

On the 21 verified codes alone:
- the gate filters 8 of the 15 E10s and wrongly filters none of the 6 real problems;
- `stuck <= 0.1` filters 10 of the 15 and wrongly filters none of the 6.

- **The gate is the usable rule.** The conservative two-question gate would have spared
  the human about 40% of backstop false alarms with no missed problem in this sample.
  `making_progress` on its own is too generous: at 0.8 it lets through 7 of 12 real
  problems, because an agent can keep building while it repeats a mistake.
- **`environment_fault` does not work.** Its mean was 0.09 on E08 and 0.12 on the
  rest. The E08 events in `3e77267c` were a frozen or unresponsive world, which leaves
  no error text for JEV to read. Environment faults need a deterministic check, such as
  the game tick not advancing, not a model.
- **It is stable and cheap.** Across repeats, a question's probability moved by a median
  of 0.010 and at most 0.090. Latency had a median of 0.51 s and a maximum of 0.77 s.
  Input was about 3,400 tokens per call. OpenRouter's guide reports under $0.0001 per
  call; this study's dollar cost is not separated in the key's usage.

## Limits

- **Small and narrow.** 36 requests and 12 real problems, all from one model (Haiku 5.5),
  one cell and one trigger (the backstop). Repeated-error requests and other models are
  untested.
- **Thresholds chosen in-sample.** The cut-offs were read off this data, so "0 of 12
  wrongly filtered" is optimistic. They need checking on new runs, such as the GPT 6
  Luna and new-wording Haiku runs, once those are coded.
- **15 labels are drafts.** Both misses under `stuck <= 0.1` are on unverified codes.
- **Same information as the coder.** Codes were drafted from the ~5 steps before each
  request, which is close to what JEV saw. Agreement may be higher than against an
  independent coder.

## If it goes into E2

The study points to one design:
- a switchable JEV *backstop filter*: when the backstop fires, call JEV with the gate's
  two questions, and skip the human only when both pass;
- a deterministic check for environment faults;
- every decision recorded (questions, probabilities, outcome, tokens).

It targets E10, the most common code in the catalogue so far (24 of 36). Open
questions for the research lead:
- JEV would be a third model across every cell. Do its tokens count towards each run's
  budget?
- Does a filtered request count as "human help avoided" in the metrics?
