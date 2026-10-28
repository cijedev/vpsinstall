"""Persisted installer progress so a long Virtualmin run can be resumed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class InstallState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"completed": [], "started_at": None, "finished_at": None}

    def load(self) -> None:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except json.JSONDecodeError:
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    def mark_started(self) -> None:
        if not self.data.get("started_at"):
            self.data["started_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def is_done(self, step: str) -> bool:
        return step in self.data.get("completed", [])

    def mark_done(self, step: str) -> None:
        completed = list(self.data.get("completed", []))
        if step not in completed:
            completed.append(step)
        self.data["completed"] = completed
        self.data["last_step"] = step
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def mark_finished(self) -> None:
        self.data["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def reset(self) -> None:
        self.data = {"completed": [], "started_at": None, "finished_at": None}
        self.save()
