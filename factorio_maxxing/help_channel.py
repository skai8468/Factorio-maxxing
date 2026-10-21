"""The file handoff between the panel human backend and the watcher (D72).

When the harness asks for help with ``human: "panel"``, ``PanelHuman`` posts a request
file here and waits; the watcher shows it in the game panel, and writes the answer the
watcher typed back as an answer file. The harness never touches the game - the watcher
holds the only connection to it - and the human still provides text only.

Both files are JSON, written atomically (temporary file, then rename), so a reader
never sees half a file:

    request.json   {"id": "...", "goal": "...", "reason": "...", "asked_at": <unix time>}
    answer.json    {"id": "...", "text": "..." | null}

An answer counts only if its ``id`` matches the open request, so a stale answer from
an earlier request or run can never be taken for a new one.
"""

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUEST = "request.json"
ANSWER = "answer.json"


@dataclass(frozen=True)
class Request:
    id: str
    goal: str
    reason: str
    asked_at: float


class HelpChannel:
    """One directory, at most one open request."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    # --- the harness's side ---

    def post(self, goal: str, reason: str, asked_at: float) -> Request:
        """Open a request, replacing any stale one, and clear any stale answer."""
        self._remove(ANSWER)
        request = Request(uuid.uuid4().hex, goal, reason, asked_at)
        self._write(REQUEST, request.__dict__)
        return request

    def take_answer(self, request: Request) -> tuple[bool, str | None]:
        """(answered, text) for ``request``. Taking an answer closes the request."""
        data = self._read(ANSWER)
        if not data or data.get("id") != request.id:
            return False, None
        self.close()
        text = data.get("text")
        return True, text if isinstance(text, str) else None

    def close(self) -> None:
        self._remove(REQUEST)
        self._remove(ANSWER)

    # --- the watcher's side ---

    def pending(self) -> Request | None:
        """The open request, unless it has already been answered."""
        data = self._read(REQUEST)
        if not data or not data.get("id"):
            return None
        answered = self._read(ANSWER)
        if answered and answered.get("id") == data["id"]:
            return None
        return Request(
            str(data["id"]),
            str(data.get("goal") or ""),
            str(data.get("reason") or ""),
            float(data.get("asked_at") or 0.0),
        )

    def answer(self, request_id: str, text: str | None) -> bool:
        """Answer the open request, verbatim; False if it is no longer open."""
        data = self._read(REQUEST)
        if not data or data.get("id") != request_id:
            return False
        self._write(ANSWER, {"id": request_id, "text": text})
        return True

    # --- files ---

    def _read(self, name: str) -> dict[str, Any] | None:
        try:
            data = json.loads((self.directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, name: str, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".{name}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.directory / name)

    def _remove(self, name: str) -> None:
        try:
            (self.directory / name).unlink()
        except FileNotFoundError:
            pass
