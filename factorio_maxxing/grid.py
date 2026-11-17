"""The evaluation grid's vocabulary (build-plan 5a), shared by run configs and
annotations so the two cannot disagree about what a cell or a harness is called."""

import re

GRID_CELL = re.compile(r"^(?:[1-8]'?|pilot)$")
"""Runs 1-8 (GPT 6 Astra, Claude Haiku 5.5), the primed rows 1'-4' (GPT 6.1 Sol) and
5'-8' (GPT 6 Luna) (D64, D86), or a pilot run."""

HARNESSES = ("standard", "specialized")
BUILT_HARNESSES = ("standard",)
"""The harnesses a run can use today; the specialized one arrives in E2."""
