"""Tests for the stuck-event annotations (build-plan 5b, D69).

Trajectories and workbooks are built here, so nothing depends on a recorded run; the
committed codebook workbook is read only to keep the module's codes and columns in step
with it.
"""

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from factorio_maxxing import annotations
from factorio_maxxing.annotations import (
    CODEBOOK_VERSION,
    COLUMNS,
    ERROR_CODES,
    FIELDS,
    GUIDANCE_CODES,
    export,
    main,
    read_annotations,
    read_sheet,
    row_from_sheet,
    run_facts,
    unstuck,
    unverified,
    validate,
    write_annotations,
)
from factorio_maxxing.scorer import LADDER_VERSION

RUN = "20261020T120000-abcdef12"
HINT = "Craft a lab first - place_entity fails because you have none."
HEADERS = list(COLUMNS)


def trajectory(
    steps=30,
    ore_at=12,
    events=(
        (9, "10 consecutive non-DONE verifications", HINT),
        (20, "3 repeated execution errors: x", None),
    ),
):
    """A run of ``steps`` steps; iron ore - milestone 1 - first held at ``ore_at``."""
    records = []
    index = 0
    for step in range(steps):
        inventory = {"iron-ore": 1} if ore_at is not None and step >= ore_at else {}
        records.append(
            {
                "type": "step",
                "run_id": RUN,
                "step": step,
                "goal": "g",
                "policy": "pass",
                "observation": {"inventory": inventory},
            }
        )
        records.append(
            {
                "type": "llm_call",
                "run_id": RUN,
                "step": step,
                "role": "policy",
                "model": "claude-haiku-5-5",
            }
        )
        for event_step, reason, text in events:
            if event_step == step:
                records.append(
                    {
                        "type": "intervention",
                        "run_id": RUN,
                        "step": step,
                        "stuck_reason": reason,
                        "text": text,
                        "intervention_index": index if text else None,
                        "original_step": None,
                    }
                )
                index += 1 if text else 0
    return records


def with_run_record(records, **config):
    run = {
        "type": "run",
        "run_id": RUN,
        "step": None,
        "goal": "g",
        "open_ended": True,
        "max_steps": 30,
        "config": {
            "grid_cell": "6",
            "harness": "standard",
            "human": "interactive",
            **config,
        },
        "code": {"commit": None, "dirty": None},
    }
    return [run, *records]


def write_trajectory(directory, records=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{RUN}.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records or trajectory()), encoding="utf-8"
    )
    return path


def row(**overrides):
    """A valid annotation of the step-9 event in ``trajectory()``."""
    base = {
        "run_id": RUN,
        "step": 9,
        "intervention_index": 0,
        "stuck_reason": "10 consecutive non-DONE verifications",
        "policy_model": "claude-haiku-5-5",
        "grid_cell": "6",
        "harness": "standard",
        "human": True,
        "error_category": "E05",
        "error_secondary": "E03",
        "error_note": "kept moving a lab it never had",
        "guidance_category": "G2",
        "guidance_secondary": None,
        "guidance_text": HINT,
        "guidance_mentions_api": True,
        "unstuck": "no",
        "unstuck_k": 10,
        "ladder_version": LADDER_VERSION,
        "codebook_version": CODEBOOK_VERSION,
        "annotator": "research lead",
        "verified_by": None,
        "notes": None,
    }
    base.update(overrides)
    return base


def declined(**overrides):
    """A valid annotation of the unanswered step-20 event."""
    values = {
        "step": 20,
        "intervention_index": None,
        "stuck_reason": "3 repeated execution errors: x",
        "error_category": "E06",
        "error_secondary": None,
        "guidance_category": None,
        "guidance_text": None,
        "guidance_mentions_api": None,
        "unstuck": "n/a",
    }
    return row(**{**values, **overrides})


FACTS = run_facts(trajectory())


def make_workbook(path, sheets):
    """A minimal .xlsx whose sheets hold inline-string cells: {name: [[cell, ...]]}."""

    def column(index):
        name = ""
        index += 1
        while index:
            index, rest = divmod(index - 1, 26)
            name = chr(65 + rest) + name
        return name

    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    names = list(sheets)
    with zipfile.ZipFile(path, "w") as book:
        book.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main_ns}" xmlns:r="{rel_ns}"><sheets>'
            + "".join(
                f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                for i, n in enumerate(names)
            )
            + "</sheets></workbook>",
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">'
            + "".join(
                f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml"/>'
                for i in range(len(names))
            )
            + "</Relationships>",
        )
        for i, name in enumerate(names):
            rows = []
            for r, values in enumerate(sheets[name], 1):
                cells = "".join(
                    f'<c r="{column(c)}{r}" t="inlineStr">'
                    f"<is><t>{escape(str(v))}</t></is></c>"
                    for c, v in enumerate(values)
                    if v not in (None, "")
                )
                rows.append(f'<row r="{r}">{cells}</row>')
            book.writestr(
                f"xl/worksheets/sheet{i + 1}.xml",
                f'<worksheet xmlns="{main_ns}"><sheetData>'
                f"{''.join(rows)}</sheetData></worksheet>",
            )
    return path


def sheet_row(**values):
    defaults = {
        "run_id": RUN,
        "grid cell (1-8)": "6",
        "harness": "standard",
        "human (yes/no)": "yes",
        "step (as recorded)": "9",
        "error code (primary)": "E05",
        "error code (secondary)": "E03",
        "error note": "kept moving a lab it never had",
        "guidance code (primary)": "G2",
        "guidance mentions API calls (yes/no)": "yes",
        "codebook version": "v0",
        "annotator": "research lead",
    }
    defaults.update(values)
    return [defaults.get(header, "") for header in HEADERS]


def declined_sheet_row(**values):
    return sheet_row(
        **{
            "step (as recorded)": "20",
            "error code (primary)": "E06",
            "error code (secondary)": "",
            "guidance code (primary)": "",
            "guidance mentions API calls (yes/no)": "",
            **values,
        }
    )


def workbook(tmp_path, *rows, k=None):
    sheets = {"Annotations": [HEADERS, *rows]}
    if k is not None:
        sheets = {
            "README": [["Error & guidance codebook"], ["k (steps)", str(k)]],
            **sheets,
        }
    return make_workbook(tmp_path / "codebook.xlsx", sheets)


# --- The unstuck rule --------------------------------------------------------------


@pytest.mark.parametrize(
    ("milestones", "last_step", "expected"),
    [
        ((15,), 100, "yes"),
        ((20,), 100, "yes"),  # step + k is inside the window
        ((21,), 100, "no"),
        ((10,), 100, "no"),  # reached at the event's own step: before the request
        ((3,), 100, "no"),
        ((), 100, "no"),
        ((), 15, "n/a"),  # the run ended before k steps had passed
        ((14,), 15, "yes"),  # a milestone before the end still counts
        ((), 20, "no"),  # exactly k steps remained
    ],
)
def test_unstuck_is_a_new_milestone_within_k_steps(milestones, last_step, expected):
    assert unstuck(10, milestones, last_step, k=10) == expected


def test_run_facts_reads_events_model_and_milestones():
    assert FACTS.run_id == RUN
    assert sorted(FACTS.interventions) == [9, 20]
    assert FACTS.policy_model == "claude-haiku-5-5"
    assert FACTS.last_step == 29
    assert FACTS.milestone_steps == (12,)


# --- Validation --------------------------------------------------------------------


def test_a_complete_valid_annotation_has_no_problems():
    assert validate([row(unstuck="yes"), declined()], FACTS) == []


def test_every_recorded_stuck_event_needs_a_row():
    problems = validate([row(unstuck="yes")], FACTS)
    assert problems == [f"{RUN}: stuck events not annotated at steps 20"]


def test_completeness_can_be_waived_while_annotating():
    assert validate([row(unstuck="yes")], FACTS, complete=False) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"error_category": "E99"}, "unknown error code 'E99'"),
        ({"error_category": None}, "a primary error code is required"),
        ({"error_secondary": "E05"}, "secondary error code repeats the primary"),
        ({"guidance_category": "G9"}, "unknown guidance code 'G9'"),
        ({"guidance_category": None}, "a primary guidance code is required"),
        (
            {"guidance_mentions_api": None},
            "guidance mentions API calls must be yes or no",
        ),
        ({"human": False}, "guidance was recorded, but human is no"),
        ({"error_category": "E09", "error_note": ""}, "E09 (Other) needs an error note"),
        ({"guidance_category": "G5"}, "G5 (Other) needs a note"),
        ({"grid_cell": "9"}, "grid cell must be"),
        ({"harness": "fancy"}, "harness must be one of"),
        ({"codebook_version": "v1"}, "codebook version must be v0"),
        ({"annotator": " "}, "annotator is required"),
        ({"unstuck": "maybe"}, "unstuck must be one of"),
        ({"step": 10}, "the trajectory records no stuck event at this step"),
        ({"run_id": "other-run"}, f"belongs to run {RUN}"),
        (
            {"guidance_text": HINT + " (edited)"},
            "guidance_text differs from the trajectory",
        ),
        ({"stuck_reason": "tidied"}, "stuck_reason differs from the trajectory"),
        ({"policy_model": "claude-opus-5-5"}, "policy_model differs from the trajectory"),
        ({"unstuck": "no"}, "unstuck is 'yes' by the Scorer, not 'no'"),
        ({"ladder_version": "v9"}, "ladder version must be"),
    ],
)
def test_a_bad_row_is_reported(changes, message):
    base = row(unstuck="yes")
    base.update(changes)
    problems = validate([base, declined()], FACTS)
    assert any(message in p for p in problems), problems


def test_codes_given_for_guidance_that_never_came_are_reported():
    problems = validate([row(unstuck="yes"), declined(guidance_category="G1")], FACTS)
    assert problems == [
        f"{RUN} step 20: guidance_category given, but no guidance was given"
    ]


@pytest.mark.parametrize("grid_cell", ["1", "8", "5'", "8'", "pilot"])
def test_grid_cells_cover_both_budget_rows_and_pilots(grid_cell):
    assert validate([row(unstuck="yes", grid_cell=grid_cell), declined()], FACTS) == []


def test_a_stuck_event_annotated_twice_is_reported():
    problems = validate([row(unstuck="yes"), row(unstuck="yes"), declined()], FACTS)
    assert f"{RUN} step 9: annotated more than once" in problems


def test_unknown_and_missing_fields_are_reported():
    extra = {**row(unstuck="yes"), "mood": "good"}
    short = {k: v for k, v in declined().items() if k != "annotator"}
    problems = validate([extra, short], FACTS)
    assert f"{RUN} step 9: unknown field(s): mood" in problems
    assert f"{RUN} step 20: missing field(s): annotator" in problems


def test_written_rows_read_back_identically(tmp_path):
    path = tmp_path / "a.jsonl"
    write_annotations(path, [row(unstuck="yes"), declined()])
    rows = read_annotations(path)
    assert rows == [row(unstuck="yes"), declined()]
    assert list(rows[0]) == list(FIELDS)


# --- Export from the workbook ------------------------------------------------------


def test_export_writes_one_file_per_run_with_recorded_fields_filled(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(tmp_path, sheet_row(), declined_sheet_row())
    written, problems = export(book, tmp_path / "traj", tmp_path / "out")

    assert problems == []
    assert written == [tmp_path / "out" / f"{RUN}.jsonl"]
    rows = read_annotations(written[0])
    assert rows == [row(unstuck="yes"), declined()]


def test_export_takes_guidance_text_from_the_trajectory_not_the_sheet(tmp_path):
    """The trajectory is the source of truth for intervention text (CLAUDE.md)."""
    write_trajectory(tmp_path / "traj")
    truncated = sheet_row(**{"guidance text (verbatim)": "Craft a lab first [...]"})
    book = workbook(tmp_path, truncated, declined_sheet_row())
    written, _ = export(book, tmp_path / "traj", tmp_path / "out")
    assert read_annotations(written[0])[0]["guidance_text"] == HINT


def test_export_reports_an_entered_unstuck_that_disagrees(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(
        tmp_path,
        sheet_row(**{"unstuck within k (yes/no/n/a)": "no"}),
        declined_sheet_row(),
    )
    written, problems = export(book, tmp_path / "traj", tmp_path / "out")
    assert written == []
    assert problems == [
        f"{RUN} step 9: unstuck entered as 'no', but the Scorer gives 'yes'"
    ]


def test_an_entered_unstuck_that_agrees_is_accepted(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(
        tmp_path,
        sheet_row(**{"unstuck within k (yes/no/n/a)": "yes"}),
        declined_sheet_row(**{"unstuck within k (yes/no/n/a)": "n/a"}),
    )
    assert export(book, tmp_path / "traj", tmp_path / "out")[1] == []


def test_export_writes_nothing_for_a_run_with_a_bad_row(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(
        tmp_path, sheet_row(**{"error code (primary)": "E99"}), declined_sheet_row()
    )
    written, problems = export(book, tmp_path / "traj", tmp_path / "out")
    assert written == []
    assert problems == [f"{RUN} step 9: unknown error code 'E99'"]
    assert not (tmp_path / "out").exists()


def test_export_writes_an_incomplete_run_and_says_so(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(tmp_path, sheet_row())
    written, problems = export(book, tmp_path / "traj", tmp_path / "out")
    assert len(written) == 1
    assert problems == [f"{RUN}: stuck events not annotated at steps 20"]


def test_export_reads_k_from_the_readme_sheet(tmp_path):
    """Ore at step 12 is 3 steps after the step-9 event: k=2 makes it 'no'."""
    write_trajectory(tmp_path / "traj")
    book = workbook(tmp_path, sheet_row(), declined_sheet_row(), k=2)
    written, problems = export(book, tmp_path / "traj", tmp_path / "out")
    assert problems == []
    first, second = read_annotations(written[0])
    assert (first["unstuck"], first["unstuck_k"]) == ("no", 2)
    assert second["unstuck"] == "no"


def test_export_names_a_missing_trajectory(tmp_path):
    book = workbook(tmp_path, sheet_row())
    written, problems = export(book, tmp_path / "missing", tmp_path / "out")
    assert written == []
    assert len(problems) == 1 and "cannot read its trajectory" in problems[0]


def test_export_rejects_a_step_that_is_not_a_number_and_a_bad_yes_no(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(
        tmp_path,
        sheet_row(**{"step (as recorded)": "nine", "human (yes/no)": "y"}),
    )
    _, problems = export(book, tmp_path / "traj", tmp_path / "out")
    assert f"{RUN} step -1: step 'nine' is not a step number" in problems
    assert f"{RUN} step -1: human must be yes or no, not 'y'" in problems


def test_blank_sheet_rows_are_skipped(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(tmp_path, sheet_row(), [""] * len(HEADERS), declined_sheet_row())
    assert export(book, tmp_path / "traj", tmp_path / "out")[1] == []


def test_numeric_steps_written_by_excel_are_read_as_integers():
    facts = run_facts(trajectory())
    built, problems = row_from_sheet(
        dict(zip(HEADERS, sheet_row(**{"step (as recorded)": "9.0"}), strict=True)),
        facts,
    )
    assert problems == []
    assert built["step"] == 9


# --- Command line -----------------------------------------------------------------


def test_cli_export_then_validate(tmp_path, capsys):
    write_trajectory(tmp_path / "traj")
    book = workbook(tmp_path, sheet_row(), declined_sheet_row())
    out = tmp_path / "out"
    assert (
        main(
            [
                "export",
                str(book),
                "--trajectory-dir",
                str(tmp_path / "traj"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "validate",
                str(out / f"{RUN}.jsonl"),
                "--trajectory-dir",
                str(tmp_path / "traj"),
            ]
        )
        == 0
    )
    assert "valid:" in capsys.readouterr().out


def test_cli_validate_fails_on_an_edited_hint(tmp_path, capsys):
    write_trajectory(tmp_path / "traj")
    path = tmp_path / f"{RUN}.jsonl"
    write_annotations(path, [row(unstuck="yes", guidance_text="paraphrased"), declined()])
    exit_code = main(["validate", str(path), "--trajectory-dir", str(tmp_path / "traj")])
    assert exit_code == 1
    assert "guidance_text differs from the trajectory" in capsys.readouterr().err


def test_cli_export_of_a_missing_workbook_exits_2(tmp_path, capsys):
    assert main(["export", str(tmp_path / "none.xlsx")]) == 2
    assert "error:" in capsys.readouterr().err


# --- The committed workbook --------------------------------------------------------

CODEBOOK = Path("docs/codebook-v0.xlsx")


def test_the_codes_match_the_workbook():
    errors = {r["Code"]: r["Category"] for r in read_sheet(CODEBOOK, "Error codebook")}
    guidance = {
        r["Code"]: r["Category"] for r in read_sheet(CODEBOOK, "Guidance codebook")
    }
    assert errors == ERROR_CODES
    assert guidance == GUIDANCE_CODES


def test_every_mapped_column_is_in_the_annotations_sheet():
    with zipfile.ZipFile(CODEBOOK) as book:
        grid = annotations._sheet_grid(
            book,
            annotations._sheet_part(book, "Annotations"),
            annotations._shared_strings(book),
        )
    assert set(COLUMNS) <= set(grid[0].values())


def test_the_workbook_sets_k():
    assert annotations._unstuck_k(CODEBOOK) == 10


def test_the_worked_examples_use_codebook_codes():
    rows = read_sheet(CODEBOOK, "Worked examples")
    assert len(rows) == 4
    for cells in rows:
        assert cells["error code (primary)"] in ERROR_CODES
        assert cells.get("error code (secondary)", "") in ("", *ERROR_CODES)
        assert cells.get("guidance code (primary)", "") in ("", *GUIDANCE_CODES)
        assert cells["codebook version"] == CODEBOOK_VERSION


# --- Invariant --------------------------------------------------------------------


@pytest.mark.parametrize(
    "module", ["loop", "run", "stuck", "verifier", "context", "human", "trajectory"]
)
def test_no_control_flow_module_reads_annotations(module):
    """Annotations are post-hoc and never read by control flow (CLAUDE.md)."""
    source = Path(f"factorio_maxxing/{module}.py").read_text(encoding="utf-8")
    assert "annotations" not in source


# --- Against the run record (D70) -----------------------------------------------


def test_a_run_record_that_agrees_is_accepted():
    facts = run_facts(with_run_record(trajectory()))
    assert facts.config["grid_cell"] == "6"
    assert validate([row(unstuck="yes"), declined()], facts) == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"grid_cell": "2"}, "grid cell is '2' in the run record"),
        ({"harness": "specialized"}, "harness is 'specialized' in the run record"),
        ({"human": "none"}, "human backend is 'none' in the run record"),
    ],
)
def test_an_annotation_contradicting_the_run_record_is_reported(config, message):
    facts = run_facts(with_run_record(trajectory(), **config))
    problems = validate([row(unstuck="yes"), declined()], facts)
    assert any(message in p for p in problems), problems


def test_a_run_outside_the_grid_does_not_constrain_the_cell():
    facts = run_facts(with_run_record(trajectory(), grid_cell=""))
    assert (
        validate(
            [row(unstuck="yes", grid_cell="pilot"), declined(grid_cell="pilot")], facts
        )
        == []
    )


def test_a_trajectory_from_before_the_run_record_still_validates():
    assert FACTS.config is None


# --- Drafts and verification (D74) --------------------------------------------------


def test_a_draft_row_is_valid_but_awaits_verification():
    rows = [row(unstuck="yes", annotator="Claude (draft)"), declined()]
    assert validate(rows, FACTS) == []
    assert unverified(rows) == [9, 20]


def test_a_verified_row_no_longer_awaits_verification():
    rows = [row(unstuck="yes", verified_by="SK"), declined(verified_by="SK")]
    assert unverified(rows) == []


def test_a_blank_verifier_is_rejected():
    problems = validate([row(unstuck="yes", verified_by="  "), declined()], FACTS)
    assert f"{RUN} step 9: verified_by must be a name or empty" in problems


def test_export_carries_the_verifier_from_the_sheet(tmp_path):
    write_trajectory(tmp_path / "traj")
    book = workbook(tmp_path, sheet_row(**{"verified by": "SK"}), declined_sheet_row())
    written, _ = export(book, tmp_path / "traj", tmp_path / "out")
    first, second = read_annotations(written[0])
    assert first["verified_by"] == "SK"
    assert second["verified_by"] is None


# --- E10, no error (D75) --------------------------------------------------------------

BACKSTOP_EVENTS = (
    (9, "10 consecutive non-DONE verifications", HINT),
    (20, "20 steps without a request for help", None),
)


def test_e10_codes_a_backstop_request_with_nothing_wrong():
    facts = run_facts(trajectory(events=BACKSTOP_EVENTS))
    rows = [
        row(unstuck="yes"),
        declined(
            error_category="E10", stuck_reason="20 steps without a request for help"
        ),
    ]
    assert validate(rows, facts) == []


def test_e10_is_refused_on_a_request_the_backstop_did_not_raise():
    problems = validate([row(unstuck="yes"), declined(error_category="E10")], FACTS)
    assert f"{RUN} step 20: E10 (No error) applies only to a backstop request" in problems


def test_e10_takes_no_secondary_code():
    facts = run_facts(trajectory(events=BACKSTOP_EVENTS))
    rows = [
        row(unstuck="yes"),
        declined(
            error_category="E10",
            error_secondary="E06",
            stuck_reason="20 steps without a request for help",
        ),
    ]
    assert f"{RUN} step 20: E10 (No error) takes no secondary code" in validate(
        rows, facts
    )


def test_the_backstop_reason_is_the_detectors():
    from factorio_maxxing.stuck import StepBackstopDetector

    _, reason = StepBackstopDetector(20).is_stuck([("x", "o")] * 20, [], [], [])
    assert annotations.BACKSTOP_REASON in reason


def test_saving_restores_the_code_dropdowns(tmp_path):
    """D74: Excel stores cross-sheet drop-downs as extensions openpyxl drops on load."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = annotations.SHEET
    path = tmp_path / "book.xlsx"
    annotations.save_workbook(book, path)
    saved = openpyxl.load_workbook(path)[annotations.SHEET]
    found = {str(dv.sqref): dv.formula1 for dv in saved.data_validations.dataValidation}
    assert found == annotations.CODE_DROPDOWNS
