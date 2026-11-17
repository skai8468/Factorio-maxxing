# Bug log

Defects in the harness, FLE or the tooling around them, found while building and piloting
(D74). Until the standard harness is frozen (`standard-harness-v1`), every issue met in a
live run is logged here **and** its stuck events are coded in the codebook workbook,
marked *pre-freeze* so grid analysis can exclude them. Design decisions stay in
`decisions.md`; this file says what broke, what it did to the data, and where it was fixed.

| Field | Meaning |
|---|---|
| Component | `FLE` (patched via `scripts/patch_fle.sh`), `harness`, or `watcher` |
| Data effect | What it did to recorded runs - whether any trajectory is compromised |
| Codebook | Workbook rows the bug produced, if any |

---

## B1 - FLE drops every lab while a hyphenated technology is researched

- **Found:** 2026-10-21, first grid run, cell 5 (`20261021T162318-3e77267c`), from step 45.
- **Component:** FLE 0.4.8 @ `e2a829d`, `env/mods/serialize.lua`.
- **Symptom:** the agent built lab after lab with no power; the panel listed no labs.
  `place_entity` said "Could not place lab" while the lab left the inventory;
  `get_entity` raised `'list' object has no attribute 'items'`.
- **Cause:** a lab's current research was serialized unquoted; FLE's parser read
  `logistic-science-pack` as a subtraction, the lab came back as a list, and
  `get_entities` silently skipped it.
- **Data effect:** the run's steps 45-137 measure the bug, not the agent (28 invisible,
  unpowered labs). Any run researching a hyphenated technology with labs placed is
  affected.
- **Fix:** edit 3 of `scripts/patch_fle.sh` - commit `8db1ff8`, D73. Verified on the live
  response: 0 of 28 labs recovered before, 28 of 28 after.
- **Codebook:** Annotations rows for steps 59, 79, 99, 119 of the run (E08, E06 secondary).
- **Status:** fixed; worth reporting upstream.

## B2 - The watch panel never appeared

- **Found:** 2026-10-21, same run.
- **Component:** watcher (`factorio_maxxing/watch.py`).
- **Symptom:** camera and cues worked; no panel.
- **Cause:** `--trajectory-dir` defaulted to `trajectories` relative to the working
  directory; the watcher is started from `~/fle-work`, the harness writes into the
  repository.
- **Data effect:** none - the watcher only reads.
- **Fix:** default resolved from the repository - commit `40a1948`, D71.
- **Status:** fixed.

## B3 - Panel text cut at 60 characters

- **Found:** 2026-10-21, same run.
- **Component:** watcher.
- **Symptom:** the goal and the agent's intent could not be read in full.
- **Cause:** every panel field was cut to 60 characters with an ellipsis.
- **Data effect:** none.
- **Fix:** goal shown whole, free text up to 240 - commit `40a1948`, D71.
- **Status:** fixed.

## B4 - A help request was invisible until answered

- **Found:** 2026-10-21, review of the human interface for the grid's human cells.
- **Component:** harness (`loop.py`, `trajectory.py`).
- **Symptom:** while the harness waited for a human, nothing in the trajectory or panel
  said so (D63's stated limit).
- **Cause:** the intervention record was written only after the answer.
- **Data effect:** none on recorded runs (no human cell has run since D63).
- **Fix:** `help_request` record before asking - commit `a6d95b1`, D72.
- **Status:** fixed.

## B5 - Time to answer was not recorded

- **Found:** 2026-10-21, same review.
- **Component:** harness.
- **Symptom:** build-plan 5c requires logging the time taken to answer each request;
  nothing recorded it.
- **Data effect:** human-effort tables for earlier human runs lack answer times.
- **Fix:** `answer_seconds` on every intervention record - commit `a6d95b1`, D72.
- **Status:** fixed.

## B6 - FLE's production scores look broken (not a bug: what the fields mean)

- **Found:** 2026-10-21, cell-5 run `20261021T172150-ba5dcf0a`.
- **Component:** FLE (`env/gym_env/environment.py`), as read by the harness.
- **Symptom:** the observation's `score` was 0 at most steps; `automated_score` rose to
  ~3,500 by step 75, then fell to -500,696 by step 300.
- **Cause:** neither is broken. `score` is FLE's *per-step change* in production score
  (`production_score - initial_score - error_penalty`, penalty 0), not a total; the game
  is paused between steps, so most steps change nothing. Summed over the run it is FLE's
  cumulative production score: **-356** here (the step records' `reward` sums to the same).
  `automated_score` is cumulative but credits only machine production; on our reading of
  FLE's score (value made minus value consumed - not yet confirmed in its source), labs
  consuming *hand-crafted* science packs count against it, while the hand-crafting itself
  is excluded - hence the dive in a run that crafts all its science by hand.
- **Data effect:** none on recorded data - the per-step values are recorded faithfully.
  But FLE's production score rewards making and keeping items; research *consumes* them,
  so a run climbing the tech tree (our goal, D67) can score negative. FLE's leaderboard
  figures (e.g. 293,206) are this cumulative score under FLE's own goal ("the highest
  automatic production score rate").
- **Fix:** none needed in code. **Open for the research lead:** whether FLE production
  score stays a secondary metric (build-plan 5), and if so as the sum of per-step score.
- **Status:** explained.

## B7 - A trajectory record was overwritten by the next one

- **Found:** 2026-10-21, cell-5 run `20261021T193258-2cd7aa8c` (D78), when scoring it.
- **Component:** harness (`trajectory.TrajectoryRecorder`), on WSL writing to `/mnt/c`.
- **Symptom:** the Scorer could not read the trajectory - two lines were not JSON.
- **Cause:** the step-227 record (~88 KB) broke at byte 65,512 - one 64 KiB 9p message
  less its 24-byte header - and the next record (its 320-byte model call plus newline)
  sat on top of bytes 65,512-65,832 of it: an *overwrite*, not an insertion. The recorder
  opened the file in append mode; under WSL the trajectory lives on the Windows drive
  (9p), where a large O_APPEND write goes out in pieces and the next append was placed
  at a stale end-of-file. Once in 260 records over 64 KiB in that run; no other recorded
  trajectory (43 checked) is affected.
- **Data effect:** 321 bytes of step 227 lost - the tail of the `processing-unit`
  technology entry and the whole `production-science-pack` entry of its research list.
  **Repaired byte-exactly:** both technologies are unresearched and identical at steps
  226 and 228, the text rebuilt from step 228 is exactly 321 bytes, and step 227's
  research list then equals its neighbours'. The damaged file is kept as
  `trajectories/20261021T193258-2cd7aa8c.jsonl.orig`. No scored field was affected.
- **Fix:** the recorder opens the file without O_APPEND and positions at its end once,
  so every write lands at an explicit offset; an existing file is still extended, never
  truncated. Tests cover a >64 KiB record followed by a small one.
- **Status:** fixed (from the next run on).


## B8 - A provider refusal was recorded as an empty step

- **Found:** 2026-11-17, cell-1 Opus run `20261117T004333-d8b88453`, the first run
  after the freeze (D82), while watching it.
- **Component:** harness (`llm.APIClient`, OpenRouter route) and the provider.
- **Symptom:** the step count rose, but the agent never acted. Every policy call
  returned 0 output tokens, and every step had an empty program. The summarizer's
  calls, which use a short prompt, returned text.
- **Cause:** Opus 5.5 refused the policy prompt (~127K characters: instructions plus
  API reference) with `finish_reason: content_filter` and the refusal "blocked as it
  seems to violate Anthropic's Terms of Service restrictions on reverse engineering or
  duplicating model outputs". This was reproduced on Amazon Bedrock and on Google
  Vertex. The same prompt shape with a short text passes, so it is a false positive on
  the full prompt. OpenRouter cannot route to Anthropic's own endpoint for this account,
  because the account's zero-data-retention setting excludes it, which leaves only
  those two. Haiku 5.5 and GPT 6 Luna never refused the same prompt. The harness reads
  only `message.content`, so a refusal arrived as an empty reply and was recorded as a
  step with no code.
- **Data effect:** that run's 32 steps are void: no action was taken, and it cost
  ~$0.86. It is not grid data. No earlier run has a policy call with 0 output tokens.
- **Fix:** `standard-harness-v1.1` (D83).
  - `LLMResponse` carries the provider's `stop_reason` and `refusal`.
  - `llm_call` records write both fields.
  - A refused policy call ends the run with `model refused: ...` before the step is
    taken.

  Running Opus 5.5 still needs a route that accepts the prompt. None is available on
  this key (see below).
- **Other models (2026-11-17).** The same step-1 prompt was sent once through
  OpenRouter to each of these candidates. A model "accepts" when it answered with a
  python block.
  - **Refused (`content_filter`):** `anthropic/claude-opus-5` and
    `anthropic/claude-sonnet-5.5`.
  - **Accepted:** `anthropic/claude-opus-4.8`, `openai/gpt-6-astra`,
    `openai/gpt-6.1-sol`, `google/gemini-4-argon` and `meta/muse-wonder`.
  - **Not available:** `google/gemini-4-carbon` is "banned" for this key (403).

  One accepted call does not show that every later step will be accepted.
- **Status:** fixed in the harness (v1.1); Opus 5.5 remains blocked on this key.

## B9 - FLE's craft_item gives up on multi-level recipes it documents as recursive

- **Found:** 2026-11-17, while coding the new-wording Haiku run `20261116T231731-65398af8`.
- **Component:** FLE (`craft_item`, 0.4.8 @ `e2a829d`).
- **Symptom:** a craft that needs intermediates more than one level down fails even
  though the raw plates are in the inventory:
  - step 97: `craft_item(lab)` failed with "still missing ingredients -
    iron-gear-wheel x2" with 152 iron plates on hand. FLE had made the belts but not the
    last gears.
  - step 116: `craft_item(logistic-science-pack, 8)` failed with "recursive crafting loop
    detected" for the inserters' gears, with 121 iron plates on hand.

  The API reference sent to the model says that for "items requiring intermediate
  resources, it will attempt to craft those first" (Recursive Crafting).
- **Cause:** not investigated. FLE's recursive crafting does not resolve every
  multi-level chain, and has a loop guard that trips on gear wheels needed at two
  levels.
- **Data effect:** a wasted or partly wasted step whenever a model relies on the
  documented behaviour. Coded E08 (environment) at Haiku requests 99 and 119. The GPT 6
  Luna run worked around it from step 236 ("Make components separately to avoid
  recursive-crafting failures"). It affects every model and both harnesses the same way.
- **Fix:** none in the frozen harness. Worth reporting upstream. The specialized harness
  (E2) could target it if it recurs in the catalogue.
- **Status:** open.

## B10 - Runs recorded `dirty: true` because of line endings only

- **Found:** 2026-11-17, on the GPT 6 Astra cell-1 run `20261117T171047-6a8934eb`.
- **Component:** tooling (editing files from Windows) and `run.py`'s code record (D70).
- **Symptom:** the run's `run` record said `"dirty": true` on commit `c5ca072`, although
  Windows git reported a clean tree.
- **Cause:** eight tracked files had been rewritten on Windows with CRLF line endings.
  Windows git (`core.autocrlf=input`) ignores the difference. The run's
  `git status` runs in WSL, which has no such setting, so it reported them as
  modified. With line endings ignored (`git diff --ignore-cr-at-eol`) there was no
  difference: the run used exactly the code of `c5ca072`, which is
  `standard-harness-v1.2` plus docs and data.
- **Data effect:** that one run's `dirty` flag is a false positive. Its harness code is
  `standard-harness-v1.2`, and it is valid grid data.
- **Fix:** the files were converted to LF and the index refreshed. Both gits now see a
  clean tree. To avoid it: write files with `\n` line endings (for example
  `newline="\n"` in Python on Windows), and check `git status` from WSL before a grid
  run.
- **Status:** fixed (for the next run).
