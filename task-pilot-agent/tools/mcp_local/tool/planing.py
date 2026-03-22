# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import re
import threading


class _PlanStore:
	"""线程安全的内存计划存储，仅维护当前会话内的一个活动计划。"""

	def __init__(self) -> None:
		self._lock = threading.Lock()
		self._plan: Optional[Dict[str, Any]] = None

	def create(self, title: str, steps: List[str]) -> Dict[str, Any]:
		with self._lock:
			parsed_steps = [_parse_step_text(i, s) for i, s in enumerate(steps, start=1)]
			self._plan = {
				"title": title,
				"steps": [
					{
						"index": idx,
						"raw": raw,
						"short": short,
						"detail": detail,
						"status": "not_started",
						"notes": "",
					}
					for idx, raw, short, detail in parsed_steps
				],
			}
			return self._snapshot()

	def update(self, title: Optional[str], steps: Optional[List[str]]) -> Dict[str, Any]:
		with self._lock:
			if self._plan is None:
				raise RuntimeError("尚未创建计划，无法更新")
			if title:
				self._plan["title"] = title
			if steps is not None:
				parsed_steps = [_parse_step_text(i, s) for i, s in enumerate(steps, start=1)]
				self._plan["steps"] = [
					{
						"index": idx,
						"raw": raw,
						"short": short,
						"detail": detail,
						"status": "not_started",
						"notes": "",
					}
					for idx, raw, short, detail in parsed_steps
				]
			return self._snapshot()

	def mark_step(self, step_index: int, step_status: str, step_notes: Optional[str]) -> Dict[str, Any]:
		with self._lock:
			if self._plan is None:
				raise RuntimeError("尚未创建计划，无法标记步骤")
			steps = self._plan["steps"]
			if not (1 <= step_index <= len(steps)):
				raise ValueError("step_index 越界")
			steps[step_index - 1]["status"] = step_status
			if step_notes is not None:
				steps[step_index - 1]["notes"] = step_notes
			return self._snapshot()

	def get(self) -> Optional[Dict[str, Any]]:
		with self._lock:
			return self._snapshot() if self._plan is not None else None

	def _snapshot(self) -> Dict[str, Any]:
		# 深拷贝语义（结构较浅，直接重建）
		plan = self._plan or {"title": "", "steps": []}
		return {
			"title": plan["title"],
			"steps": [
				{
					"index": s["index"],
					"raw": s["raw"],
					"short": s["short"],
					"detail": s["detail"],
					"status": s["status"],
					"notes": s.get("notes", ""),
				}
				for s in plan["steps"]
			],
		}


def _parse_step_text(index: int, text: str) -> Tuple[int, str, str, str]:
	"""解析形如：
	"执行顺序1. 执行任务简称（不超过6个字）：执行任务的细节描述（不超过50个字）"
	的字符串；回退策略：无法解析时保留原文到 short，detail 为空。
	"""
	short = ""
	detail = ""

	# 允许前缀的 "执行顺序X." 可选，使用中文冒号或英文冒号
	pattern = r"^(?:执行顺序\s*\d+\.\s*)?(?P<short>[^：:]{1,20})[：:](?P<detail>.+)$"
	m = re.match(pattern, text.strip())
	if m:
		short = m.group("short").strip()
		detail = m.group("detail").strip()
	else:
		short = text.strip()
		detail = ""
	return index, text, short, detail


plan_store = _PlanStore()


def create_plan(title: str, steps: List[str]) -> Dict[str, Any]:
	return plan_store.create(title=title, steps=steps)


def update_plan(title: Optional[str], steps: Optional[List[str]]) -> Dict[str, Any]:
	return plan_store.update(title=title, steps=steps)


def mark_plan_step(step_index: int, step_status: str, step_notes: Optional[str]) -> Dict[str, Any]:
	return plan_store.mark_step(step_index=step_index, step_status=step_status, step_notes=step_notes) 