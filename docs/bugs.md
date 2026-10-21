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
