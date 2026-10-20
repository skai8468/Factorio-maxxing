"""Stuck-event annotations: the error and guidance catalogue's rows (build-plan 5b).

See docs/decisions.md D69. The research lead codes stuck events in the codebook
workbook (``docs/codebook-v0.xlsx``, Annotations sheet). ``export`` turns that sheet into
``annotations/<run_id>.jsonl``, one row per stuck event, and ``validate`` checks a JSONL
file against its run's trajectory. Annotations are post-hoc and stored apart from
trajectories; nothing in the harness's control flow reads them (CLAUDE.md).

What comes from where:

- *The workbook* supplies what only a person can judge: the error and guidance codes,
  the notes, whether a human was attached, the grid cell and harness.
- *The trajectory* supplies everything recorded: ``intervention_index``,
  ``stuck_reason``, the policy model and the guidance text - copied verbatim, because
  the trajectory is the source of truth for intervention text and a spreadsheet cell
  can truncate it.
- *The Scorer* decides ``unstuck`` (D69): ``yes`` if a milestone is first reached within
  k steps after the event, ``n/a`` if the run ends sooner, ``no`` otherwise.

Usage::

    python -m factorio_maxxing.annotations export [docs/codebook-v0.xlsx]
    python -m factorio_maxxing.annotations validate annotations/<run_id>.jsonl ...
"""

import argparse
import json
import re
import sys
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from factorio_maxxing.grid import GRID_CELL, HARNESSES
from factorio_maxxing.scorer import LADDER_VERSION, score_records
from factorio_maxxing.trajectory import read_trajectory

CODEBOOK_VERSION = "v0"
WORKBOOK = Path("docs/codebook-v0.xlsx")
SHEET = "Annotations"
UNSTUCK_K = 10
"""Steps after a stuck event within which a new milestone counts as unstuck. The
workbook's README sheet holds the research lead's value; this is its fallback."""

ERROR_CODES = {
    "E01": "API misuse",
    "E02": "Spatial / placement",
    "E03": "Recipe / tech knowledge",
    "E04": "Planning / ordering",
    "E05": "Observation misread",
    "E06": "Repetition loop",
    "E07": "Verifier false DONE",
    "E08": "Environment / infra",
    "E09": "Other",
}
GUIDANCE_CODES = {
    "G1": "KNOWLEDGE",
    "G2": "DIAGNOSIS",
    "G3": "PLANNING",
    "G4": "CORRECTION",
    "G5": "OTHER",
}
"""Codebook v0, as in the workbook's codebook sheets; a test keeps the two in step."""

UNSTUCK_VALUES = ("yes", "no", "n/a")

COLUMNS = {
    "run_id": "run_id",
    "grid cell (1-8)": "grid_cell",
    "harness": "harness",
    "human (yes/no)": "human",
    "step (as recorded)": "step",
    "error code (primary)": "error_category",
    "error code (secondary)": "error_secondary",
    "error note": "error_note",
    "guidance code (primary)": "guidance_category",
    "guidance code (secondary)": "guidance_secondary",
    "guidance mentions API calls (yes/no)": "guidance_mentions_api",
    "unstuck within k (yes/no/n/a)": "unstuck",
    "codebook version": "codebook_version",
    "annotator": "annotator",
    "notes": "notes",
}
"""Workbook header -> field. The sheet's policy model, intervention_index,
stuck_reason and guidance text columns are for the annotator's eye only: export takes
those from the trajectory."""

FIELDS = (
    "run_id",
    "step",
    "intervention_index",
    "stuck_reason",
    "policy_model",
    "grid_cell",
    "harness",
    "human",
    "error_category",
    "error_secondary",
    "error_note",
    "guidance_category",
    "guidance_secondary",
    "guidance_text",
    "guidance_mentions_api",
    "unstuck",
    "unstuck_k",
    "ladder_version",
    "codebook_version",
    "annotator",
    "notes",
)
"""Every key of an annotation row, in the order it is written."""

RECORDED = ("intervention_index", "stuck_reason", "policy_model", "guidance_text")


# --- What a run recorded ----------------------------------------------------------


@dataclass(frozen=True)
class RunFacts:
    """What the trajectory and the Scorer say about a run's stuck events."""

    run_id: str
    interventions: Mapping[int, Mapping[str, Any]]
    """Step -> the intervention record written there."""
    policy_model: str | None
    last_step: int
    milestone_steps: tuple[int, ...]
    """The step at which each reached milestone first held."""
    config: Mapping[str, Any] | None = None
    """The run record's config (D70); None for a trajectory recorded before it."""


def run_facts(records: Iterable[Mapping[str, Any]], run_id: str = "") -> RunFacts:
    records = list(records)
    interventions = {
        int(r["step"]): r for r in records if r.get("type") == "intervention"
    }
    policy = next(
        (
            r.get("model")
            for r in records
            if r.get("type") == "llm_call" and r.get("role") == "policy"
        ),
        None,
    )
    steps = [int(r["step"]) for r in records if r.get("type") == "step"]
    run = next((r for r in records if r.get("type") == "run"), None)
    score = score_records(records)
    return RunFacts(
        run_id=score.run_id or run_id,
        interventions=interventions,
        policy_model=policy,
        last_step=max(steps, default=-1),
        milestone_steps=tuple(sorted(score.reached.values())),
        config=run.get("config") if run else None,
    )


def unstuck(
    step: int, milestone_steps: Sequence[int], last_step: int, k: int = UNSTUCK_K
) -> str:
    """The D69 rule: did a milestone follow the stuck event within k steps?

    A milestone first reached at the event's own step came before the request for
    help, so only later steps count.
    """
    if any(step < reached <= step + k for reached in milestone_steps):
        return "yes"
    if last_step < step + k:
        return "n/a"
    return "no"


# --- Validation -------------------------------------------------------------------


def validate(
    rows: Sequence[Mapping[str, Any]], facts: RunFacts, *, complete: bool = True
) -> list[str]:
    """Problems with one run's annotation rows; empty when they are valid.

    Checks each row's own fields, then each row against the trajectory, then - with
    ``complete`` - that every stuck event the trajectory records has exactly one row.
    """
    problems: list[str] = []
    seen: set[int] = set()
    for row in rows:
        where = f"{row.get('run_id')} step {row.get('step')}"
        step = row.get("step")
        if isinstance(step, int):
            if step in seen:
                problems.append(f"{where}: annotated more than once")
            seen.add(step)
        found = [f"{where}: {p}" for p in _row_problems(row)]
        problems.extend(found)
        if not found:
            problems.extend(f"{where}: {p}" for p in _against_trajectory(row, facts))
    if complete:
        problems += _missing(facts, seen)
    return problems


def _missing(facts: RunFacts, annotated: Iterable[int]) -> list[str]:
    missing = sorted(set(facts.interventions) - set(annotated))
    if not missing:
        return []
    steps = ", ".join(str(step) for step in missing)
    return [f"{facts.run_id}: stuck events not annotated at steps {steps}"]


def _row_problems(row: Mapping[str, Any]) -> list[str]:
    problems = []
    unknown = sorted(set(row) - set(FIELDS))
    missing = sorted(set(FIELDS) - set(row))
    if unknown:
        problems.append(f"unknown field(s): {', '.join(unknown)}")
    if missing:
        problems.append(f"missing field(s): {', '.join(missing)}")
    if problems:
        return problems

    if not isinstance(row["step"], int) or row["step"] < 0:
        problems.append("step must be a non-negative integer")
    if not GRID_CELL.match(str(row["grid_cell"] or "")):
        problems.append(
            f"grid cell must be 1-8, 5'-8' or pilot, not {row['grid_cell']!r}"
        )
    if row["harness"] not in HARNESSES:
        problems.append(f"harness must be one of {', '.join(HARNESSES)}")
    if not isinstance(row["human"], bool):
        problems.append("human must be yes or no")
    if row["codebook_version"] != CODEBOOK_VERSION:
        problems.append(f"codebook version must be {CODEBOOK_VERSION}")
    if not _text(row["annotator"]):
        problems.append("annotator is required")
    if row["unstuck"] not in UNSTUCK_VALUES:
        problems.append(f"unstuck must be one of {', '.join(UNSTUCK_VALUES)}")
    if not isinstance(row["unstuck_k"], int) or row["unstuck_k"] < 1:
        problems.append("unstuck_k must be a positive integer")

    problems += _code_problems(
        row["error_category"], row["error_secondary"], ERROR_CODES, "error"
    )
    if row["error_category"] == "E09" and not _text(row["error_note"]):
        problems.append("E09 (Other) needs an error note describing it")

    if row["guidance_text"] is None:
        for key in ("guidance_category", "guidance_secondary", "guidance_mentions_api"):
            if row[key] is not None:
                problems.append(f"{key} given, but no guidance was given")
    else:
        problems += _code_problems(
            row["guidance_category"],
            row["guidance_secondary"],
            GUIDANCE_CODES,
            "guidance",
        )
        if not isinstance(row["guidance_mentions_api"], bool):
            problems.append("guidance mentions API calls must be yes or no")
        if row["guidance_category"] == "G5" and not _text(row["notes"]):
            problems.append("G5 (Other) needs a note describing it")
        if row["human"] is False:
            problems.append("guidance was recorded, but human is no")
    return problems


def _code_problems(
    primary: Any, secondary: Any, codes: Mapping[str, str], kind: str
) -> list[str]:
    if primary is None:
        return [f"a primary {kind} code is required"]
    problems = []
    if primary not in codes:
        problems.append(f"unknown {kind} code {primary!r}")
    if secondary is not None and secondary not in codes:
        problems.append(f"unknown secondary {kind} code {secondary!r}")
    if secondary is not None and secondary == primary:
        problems.append(f"secondary {kind} code repeats the primary")
    return problems


def _against_trajectory(row: Mapping[str, Any], facts: RunFacts) -> list[str]:
    if row["run_id"] != facts.run_id:
        return [f"belongs to run {facts.run_id}"]
    event = facts.interventions.get(row["step"])
    if event is None:
        return ["the trajectory records no stuck event at this step"]
    expected = recorded_fields(event, facts)
    problems = [
        f"{key} differs from the trajectory"
        for key in RECORDED
        if row[key] != expected[key]
    ]
    computed = unstuck(
        row["step"], facts.milestone_steps, facts.last_step, row["unstuck_k"]
    )
    if row["unstuck"] != computed:
        problems.append(f"unstuck is {computed!r} by the Scorer, not {row['unstuck']!r}")
    if row["ladder_version"] != LADDER_VERSION:
        problems.append(f"ladder version must be {LADDER_VERSION}")
    return problems + _against_run_record(row, facts.config)


def _against_run_record(
    row: Mapping[str, Any], config: Mapping[str, Any] | None
) -> list[str]:
    """What the annotator says about the run must match what the run recorded (D70)."""
    if not config:
        return []
    problems = []
    cell = config.get("grid_cell")
    if cell and row["grid_cell"] != cell:
        problems.append(f"grid cell is {cell!r} in the run record")
    harness = config.get("harness")
    if harness and row["harness"] != harness:
        problems.append(f"harness is {harness!r} in the run record")
    if "human" in config and row["human"] != (config["human"] != "none"):
        problems.append(f"human backend is {config['human']!r} in the run record")
    return problems


def recorded_fields(event: Mapping[str, Any], facts: RunFacts) -> dict[str, Any]:
    """The fields an annotation row copies from the trajectory, verbatim."""
    return {
        "intervention_index": event.get("intervention_index"),
        "stuck_reason": event.get("stuck_reason"),
        "policy_model": facts.policy_model,
        "guidance_text": event.get("text"),
    }


# --- Export from the workbook -----------------------------------------------------


def row_from_sheet(
    cells: Mapping[str, str], facts: RunFacts, k: int = UNSTUCK_K
) -> tuple[dict[str, Any], list[str]]:
    """Build one annotation row from a sheet row, filling recorded fields.

    The returned problems are the ones only the sheet can have: a step that is not a
    number, a yes/no cell holding something else, or an ``unstuck`` the annotator
    entered that disagrees with the Scorer. Everything else is ``validate``'s.
    """
    values = {
        field: _blank_to_none(cells.get(header)) for header, field in COLUMNS.items()
    }
    problems = []

    step = _integer(values["step"])
    if step is None:
        problems.append(f"step {values['step']!r} is not a step number")
        step = -1
    row: dict[str, Any] = dict.fromkeys(FIELDS)
    row.update(
        run_id=values["run_id"],
        step=step,
        grid_cell=values["grid_cell"],
        harness=values["harness"],
        error_category=values["error_category"],
        error_secondary=values["error_secondary"],
        error_note=values["error_note"],
        guidance_category=values["guidance_category"],
        guidance_secondary=values["guidance_secondary"],
        codebook_version=values["codebook_version"],
        annotator=values["annotator"],
        notes=values["notes"],
        unstuck_k=k,
        ladder_version=LADDER_VERSION,
    )
    for field in ("human", "guidance_mentions_api"):
        flag, problem = _yes_no(values[field], field)
        row[field] = flag
        if problem:
            problems.append(problem)

    event = facts.interventions.get(step)
    if event is not None:
        row.update(recorded_fields(event, facts))
    row["unstuck"] = unstuck(step, facts.milestone_steps, facts.last_step, k)
    entered = values["unstuck"]
    if entered is not None and entered != row["unstuck"]:
        problems.append(
            f"unstuck entered as {entered!r}, but the Scorer gives {row['unstuck']!r}"
        )
    return row, problems


def export(
    workbook: Path,
    trajectory_dir: Path,
    out_dir: Path,
    sheet: str = SHEET,
) -> tuple[list[Path], list[str]]:
    """Write ``out_dir/<run_id>.jsonl`` for each run annotated in the sheet.

    A run's file is written only if none of its rows has a problem; a run whose stuck
    events are not all annotated yet is still written, and reported as incomplete.
    Returns the files written and every problem found.
    """
    rows = [r for r in read_sheet(workbook, sheet) if any(r.values())]
    k = _unstuck_k(workbook)
    by_run: dict[str, list[Mapping[str, str]]] = {}
    for cells in rows:
        by_run.setdefault(str(cells.get("run_id") or "").strip(), []).append(cells)

    written: list[Path] = []
    problems: list[str] = []
    for run_id, run_rows in by_run.items():
        if not run_id:
            problems.append(f"{len(run_rows)} row(s) without a run_id")
            continue
        path = trajectory_dir / f"{run_id}.jsonl"
        try:
            facts = run_facts(read_trajectory(path), run_id)
        except (OSError, ValueError) as error:
            problems.append(f"{run_id}: cannot read its trajectory ({error})")
            continue

        built, sheet_problems = [], []
        for cells in run_rows:
            row, found = row_from_sheet(cells, facts, k)
            built.append(row)
            sheet_problems += [f"{run_id} step {row['step']}: {p}" for p in found]
        row_problems = sheet_problems + validate(built, facts, complete=False)
        problems += row_problems
        if row_problems:
            continue

        built.sort(key=lambda r: r["step"])
        write_annotations(out_dir / f"{run_id}.jsonl", built)
        written.append(out_dir / f"{run_id}.jsonl")
        problems += _missing(facts, (r["step"] for r in built))
    return written, problems


def write_annotations(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps({key: row[key] for key in FIELDS}) + "\n")


def read_annotations(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --- Reading the workbook ----------------------------------------------------------

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_CELL = re.compile(r"^([A-Z]+)(\d+)$")


def read_sheet(path: Path, sheet: str) -> list[dict[str, str]]:
    """A worksheet's rows below its header, as {header: cell text}.

    Read with the standard library: the workbook's sheets are plain values and
    strings, so a spreadsheet dependency would buy nothing. Formulas yield their
    cached value.
    """
    with zipfile.ZipFile(path) as book:
        shared = _shared_strings(book)
        grid = _sheet_grid(book, _sheet_part(book, sheet), shared)
    if not grid:
        return []
    header = grid[0]
    return [
        {header[col]: value for col, value in row.items() if header.get(col)}
        for row in grid[1:]
    ]


def _sheet_part(book: zipfile.ZipFile, sheet: str) -> str:
    workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target", "") for rel in rels}
    for element in workbook.iter(f"{_MAIN}sheet"):
        if element.get("name") == sheet:
            target = targets[element.get(_REL)].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError(f"no sheet named {sheet!r}")


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in item.iter(f"{_MAIN}t"))
        for item in root.iter(f"{_MAIN}si")
    ]


def _sheet_grid(
    book: zipfile.ZipFile, part: str, shared: Sequence[str]
) -> list[dict[str, str]]:
    root = ElementTree.fromstring(book.read(part))
    grid = []
    for row in root.iter(f"{_MAIN}row"):
        cells = {}
        for cell in row.iter(f"{_MAIN}c"):
            match = _CELL.match(cell.get("r", ""))
            if match:
                cells[match.group(1)] = _cell_text(cell, shared)
        grid.append(cells)
    return grid


def _cell_text(cell: ElementTree.Element, shared: Sequence[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_MAIN}t"))
    value = cell.find(f"{_MAIN}v")
    text = value.text if value is not None and value.text is not None else ""
    if kind == "s" and text:
        return shared[int(text)]
    if kind is None and re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".")[0]
    return text


def _unstuck_k(workbook: Path) -> int:
    """The research lead's k from the README sheet, else ``UNSTUCK_K``."""
    try:
        with zipfile.ZipFile(workbook) as book:
            grid = _sheet_grid(book, _sheet_part(book, "README"), _shared_strings(book))
    except (ValueError, KeyError):
        return UNSTUCK_K
    for cells in grid:
        if cells.get("A", "").strip().startswith("k (steps)"):
            k = _integer(cells.get("B", ""))
            if k is not None and k > 0:
                return k
    return UNSTUCK_K


# --- Small parsers -----------------------------------------------------------------


def _blank_to_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _integer(value: Any) -> int | None:
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _yes_no(value: str | None, field: str) -> tuple[bool | None, str | None]:
    if value is None:
        return None, None
    lowered = value.lower()
    if lowered in ("yes", "no"):
        return lowered == "yes", None
    return None, f"{field} must be yes or no, not {value!r}"


# --- Command line ------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factorio_maxxing.annotations")
    commands = parser.add_subparsers(dest="command", required=True)

    export_cmd = commands.add_parser("export", help="workbook -> annotations/*.jsonl")
    export_cmd.add_argument("workbook", nargs="?", default=str(WORKBOOK))
    export_cmd.add_argument("--sheet", default=SHEET)
    export_cmd.add_argument("--trajectory-dir", default="trajectories")
    export_cmd.add_argument("--out", default="annotations")

    validate_cmd = commands.add_parser("validate", help="check annotation files")
    validate_cmd.add_argument("files", nargs="+")
    validate_cmd.add_argument("--trajectory-dir", default="trajectories")

    args = parser.parse_args(argv)
    if args.command == "export":
        try:
            written, problems = export(
                Path(args.workbook),
                Path(args.trajectory_dir),
                Path(args.out),
                args.sheet,
            )
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        for path in written:
            print(f"wrote {path}")
    else:
        problems = []
        for name in args.files:
            try:
                rows = read_annotations(Path(name))
                run_id = Path(name).stem
                facts = run_facts(
                    read_trajectory(Path(args.trajectory_dir) / f"{run_id}.jsonl"),
                    run_id,
                )
            except (OSError, ValueError) as error:
                problems.append(f"{name}: {error}")
                continue
            found = validate(rows, facts)
            problems += found
            if not found:
                print(f"valid: {name} ({len(rows)} stuck events)")

    for problem in problems:
        print(f"problem: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
