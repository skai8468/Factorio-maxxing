# Offline study: JEV as a filter for backstop help requests

**Date:** 2026-11-17 · **Script:** `scripts/jev_offline_study.py` · **Raw answers:**
`results.jsonl`, `held-out-luna.jsonl`, `held-out-2.jsonl` · **Full output:**
`analysis.txt`, `held-out-luna-analysis.txt`, `held-out-2-analysis.txt`,
`combined-analysis.txt` (all 75 requests, scored on the verified codes)

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

## Held-out check: GPT 6 Luna (2026-11-17)

The same questions and the same cut-offs, chosen above and not re-tuned, were applied
to the 15 help requests of the GPT 6 Luna run `20261116T222832-8ef037ca`. The study had
never seen this run. It is a different model with a different style of play: hand-mining
throughout, short programs. Its codes are drafts: 12 E10, plus one each of E04, E02 and
E03. Output: `held-out-luna.jsonl`, `held-out-luna-analysis.txt`.

| Skip the human when... | E10 filtered | Real problems wrongly filtered |
|---|---|---|
| `making_progress >= 0.8` | 9/12 | 2/3 (E04, E03) |
| `making_progress >= 0.9` | 5/12 | 0/3 |
| **gate: `making_progress >= 0.9` and `stuck <= 0.1`** | **5/12** | **0/3** |
| `stuck <= 0.1` | 9/12 | 0/3 |

- **The gate held.** It filtered 42% of false alarms, as in-sample (10 of 24), and passed
  no real problem. Across both sets it filters 15 of 36 E10s and wrongly filters 0 of
  15 real problems.
- **The margin is thin.** The E03 at step 59 scored `making_progress` 0.89 and `stuck`
  0.10, just inside both cut-offs. It was caught only because both questions had to
  pass. One real problem passing the threshold on one question alone is the expected
  failure mode, so the two-question gate is safer than either question alone, despite
  `stuck <= 0.1` filtering more here.
- **`making_progress` alone fails again.** At 0.8 it would have let the lab
  under-gathering (E04) and the locked-research attempt (E03) through.
- **Smaller state, same stability.** The state was about 2,600 input tokens per call,
  because Luna writes shorter programs. Repeat spread had a median of 0.010 and a
  maximum of 0.050.

## Second held-out check and the full catalogue (2026-11-17)

Two more runs the study had never seen were put through the same three questions, with
the same cut-offs and no re-tuning. Output: `held-out-2.jsonl`, `held-out-2-analysis.txt`.
- **The new-wording Haiku run** `20261116T231731-65398af8`: 12 E10, 2 E08, 1 E04.
- **The GPT 6 Astra run** `20261117T171047-6a8934eb`: 1 E10 and 8 real problems (5 E02,
  2 E04, 1 E03). This is the hard case for a filter. Astra kept building throughout,
  so its real problems happen while the factory is growing.

By then the research lead had verified every code (D74), including the 30 drafts the
earlier sections used, with no corrections. `analyse` now reads codes from the current
annotations, so `combined-analysis.txt` scores all 75 requests on verified codes.

| Skip the human when... | New Haiku | Astra | All 75 requests |
|---|---|---|---|
| `making_progress >= 0.8` | 11/12 E10, **3/3 real** | 1/1, **3/8 real** | 43/49, 15/26 real |
| `making_progress >= 0.9` | 9/12, **2/3 real** | 1/1, **1/8 real** | 29/49, 4/26 real |
| **gate: `making_progress >= 0.9` and `stuck <= 0.1`** | **7/12, 0/3** | **1/1, 0/8** | **23/49, 0/26** |
| `stuck <= 0.1` | 9/12, **1/3 real** (E08) | 1/1, 0/8 | 35/49, 3/26 real |

Each cell gives the E10s filtered, then the real problems wrongly filtered.

- **The gate held on every run.** Across all 75 requests it would have skipped the
  human for 23 of 49 false alarms (47%) and for none of the 26 real problems. That is
  31% fewer help requests overall. On the three held-out runs alone, it skipped 13 of 25
  false alarms and none of 14 real problems.
- **Each question alone fails on unseen data.** `making_progress >= 0.9` let through
  the first real problem of both new runs (E04 at step 19 of each) and one E08.
  `stuck <= 0.1` let through an E08. The two-question gate is what kept the error
  count at zero.
- **Astra shows why progress alone is unsafe.** With `making_progress >= 0.8`, 3 of its
  8 real problems would have been skipped. A capable agent that builds while it fails
  looks like progress. `stuck` is what separates the two, as it did for Haiku.
- **The margin is still thin.** The closest real problems to the gate:

  | Request | Code | `making_progress` | `stuck` | Why it was kept |
  |---|---|---|---|---|
  | New Haiku, step 19 | E04 | 0.91 | 0.107 | missed `stuck` by 0.007 |
  | Luna, step 59 | E03 | 0.89 | 0.10 | missed `making_progress` |
  | Astra, step 19 | E04 | 0.91 | 0.17 | `stuck` too high |

  The cut-offs should not be loosened, and a live feature should log every near miss.
- **`environment_fault` is still uninformative.** Its mean is 0.08 on E08 and 0.12 on
  everything else. Its highest values (0.3-0.4) were Astra's blocked placements (E02),
  not environment faults.
- **Cost and stability are unchanged.** Calls had a median latency of 0.5 s and a
  maximum of 1.4 s. The repeat spread had a median of 0.010. Astra's long programs are
  cut to 1,500 characters each, so its requests averaged about 3,900 input tokens.

## Limits

- **Still small.** 75 requests and 26 real problems, from three models (Haiku 5.5, Luna,
  Astra) and one trigger (the backstop). No request came from the repeated-error
  detector, so that path is untested. Sol is untested.
- **Thresholds chosen in-sample.** The cut-offs were read off the first 36 Haiku
  requests. Three held-out runs (39 requests, 14 real problems) have since passed
  without re-tuning.
- **All labels are now verified** (2026-11-17). The earlier sections' tables were
  computed while 30 of them were drafts; no code changed on verification.
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
