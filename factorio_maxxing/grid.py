"""The evaluation grid's vocabulary (build-plan 5a), shared by run configs and
annotations so the two cannot disagree about what a cell or a harness is called."""

import re

GRID_CELL = re.compile(r"^(?:[1-8]|[5-8]'|pilot)$")
"""Runs 1-8, the second budget model's 5'-8' (D64), or a pilot run."""

HARNESSES = ("standard", "specialized")
BUILT_HARNESSES = ("standard",)
"""The harnesses a run can use today; the specialized one arrives in E2."""
