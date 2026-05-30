from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PlanState:
    """Simple in-memory representation of the current plan."""

    title: str
    steps: List[str]
    step_status: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.step_status:
            self.step_status = ["not_started" for _ in self.steps]
        if not self.notes:
            self.notes = ["" for _ in self.steps]
        self._sync_lengths()

    def update(self, title: Optional[str], steps: Optional[List[str]]) -> None:
        if title:
            self.title = title
        if steps is None:
            return
        new_status: List[str] = []
        new_notes: List[str] = []
        for idx, step in enumerate(steps):
            if idx < len(self.steps) and step == self.steps[idx]:
                new_status.append(self.step_status[idx])
                new_notes.append(self.notes[idx])
            else:
                new_status.append("not_started")
                new_notes.append("")
        self.steps = steps
        self.step_status = new_status
        self.notes = new_notes
        self._sync_lengths()

    def mark_step(self, step_index: int, status: str, note: Optional[str] = None) -> None:
        if status not in {"running", "completed", "failed"}:
            raise ValueError("step status must be running/completed/failed")
        if step_index < 1 or step_index > len(self.steps):
            raise ValueError("step_index is out of range")
        idx = step_index - 1
        self.step_status[idx] = status
        if note is not None:
            self.notes[idx] = note

    def mark_finished(self) -> None:
        self.step_status = ["completed" for _ in self.steps]

    def _sync_lengths(self) -> None:
        length = len(self.steps)
        if len(self.step_status) < length:
            self.step_status.extend(["not_started"] * (length - len(self.step_status)))
        if len(self.notes) < length:
            self.notes.extend([""] * (length - len(self.notes)))
        if len(self.step_status) > length:
            self.step_status = self.step_status[:length]
        if len(self.notes) > length:
            self.notes = self.notes[:length]

    def format_plan(self) -> str:
        lines = [f"计划标题: {self.title}"]
        for idx, step in enumerate(self.steps, start=1):
            status = self.step_status[idx - 1] if idx - 1 < len(self.step_status) else "not_started"
            note = self.notes[idx - 1] if idx - 1 < len(self.notes) else ""
            line = f"{idx}. [{status}] {step}"
            if note:
                line += f"\n   备注: {note}"
            lines.append(line)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "steps": self.steps,
            "step_status": self.step_status,
            "notes": self.notes,
        }
