from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlanState:
    """Simple in-memory representation of the current plan."""

    title: str
    steps: List[str]
    step_status: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    evidence: List[List[Dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.step_status:
            self.step_status = ["not_started" for _ in self.steps]
        if not self.notes:
            self.notes = ["" for _ in self.steps]
        if not self.evidence:
            self.evidence = [[] for _ in self.steps]
        self._sync_lengths()

    def update(self, title: Optional[str], steps: Optional[List[str]]) -> None:
        if title:
            self.title = title
        if steps is None:
            return
        new_status: List[str] = []
        new_notes: List[str] = []
        new_evidence: List[List[Dict[str, Any]]] = []
        for idx, step in enumerate(steps):
            if idx < len(self.steps) and step == self.steps[idx]:
                new_status.append(self.step_status[idx])
                new_notes.append(self.notes[idx])
                new_evidence.append(self.evidence[idx])
            else:
                new_status.append("not_started")
                new_notes.append("")
                new_evidence.append([])
        self.steps = steps
        self.step_status = new_status
        self.notes = new_notes
        self.evidence = new_evidence
        self._sync_lengths()

    def mark_step(
        self,
        step_index: int,
        status: str,
        note: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if status not in {"running", "completed", "failed", "waiting_input", "skipped"}:
            raise ValueError("step status must be running/completed/failed/waiting_input/skipped")
        if step_index < 1 or step_index > len(self.steps):
            raise ValueError("step_index is out of range")
        idx = step_index - 1
        self.step_status[idx] = status
        if note is not None:
            self.notes[idx] = note
        if evidence is not None:
            self.evidence[idx] = evidence

    def add_step(
        self,
        step: str,
        *,
        position: Optional[int] = None,
        note: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        step_text = str(step or "").strip()
        if not step_text:
            raise ValueError("step must not be empty")
        insert_at = len(self.steps)
        if position is not None:
            insert_at = max(0, min(position - 1, len(self.steps)))
        self.steps.insert(insert_at, step_text)
        self.step_status.insert(insert_at, "not_started")
        self.notes.insert(insert_at, str(note or ""))
        self.evidence.insert(insert_at, evidence or [])
        self._sync_lengths()
        return insert_at + 1

    def skip_step(
        self,
        step_index: int,
        *,
        note: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.mark_step(
            step_index,
            "skipped",
            note if note is not None else "skipped",
            evidence=evidence,
        )

    def mark_finished(self) -> None:
        self.step_status = ["completed" for _ in self.steps]

    def _sync_lengths(self) -> None:
        length = len(self.steps)
        if len(self.step_status) < length:
            self.step_status.extend(["not_started"] * (length - len(self.step_status)))
        if len(self.notes) < length:
            self.notes.extend([""] * (length - len(self.notes)))
        if len(self.evidence) < length:
            self.evidence.extend([[] for _ in range(length - len(self.evidence))])
        if len(self.step_status) > length:
            self.step_status = self.step_status[:length]
        if len(self.notes) > length:
            self.notes = self.notes[:length]
        if len(self.evidence) > length:
            self.evidence = self.evidence[:length]

    def format_plan(self) -> str:
        lines = [f"计划标题: {self.title}"]
        for idx, step in enumerate(self.steps, start=1):
            status = self.step_status[idx - 1] if idx - 1 < len(self.step_status) else "not_started"
            note = self.notes[idx - 1] if idx - 1 < len(self.notes) else ""
            line = f"{idx}. [{status}] {step}"
            if note:
                line += f"\n   备注: {note}"
            evidence_items = self.evidence[idx - 1] if idx - 1 < len(self.evidence) else []
            if evidence_items:
                evidence_text = "; ".join(
                    str(item.get("summary") or item.get("content") or item)
                    for item in evidence_items[:3]
                )
                line += f"\n   证据: {evidence_text}"
            lines.append(line)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "steps": self.steps,
            "step_status": self.step_status,
            "notes": self.notes,
            "evidence": self.evidence,
        }
