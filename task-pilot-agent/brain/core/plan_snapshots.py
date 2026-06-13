from __future__ import annotations

from typing import Any, Dict, Optional

from brain.core.run_events import PLAN_EVENT_TYPES
from brain.core.sanitization import sanitize_payload


LATEST_PLAN_METADATA_KEY = "latestPlan"
LATEST_PLAN_EVENT_TYPE_METADATA_KEY = "latestPlanEventType"
LATEST_PLAN_UPDATED_AT_METADATA_KEY = "latestPlanUpdatedAt"
PLAN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def plan_payload_from_event_payload(event_payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(event_payload, dict):
        return None
    plan = event_payload.get("plan")
    if isinstance(plan, dict):
        return plan
    result_map = event_payload.get("resultMap")
    if isinstance(result_map, dict) and isinstance(result_map.get("plan"), dict):
        return result_map["plan"]
    if "steps" in event_payload or "step_status" in event_payload:
        return event_payload
    return None


def latest_plan_metadata_fields(
    event_type: str,
    event_payload: Any,
    updated_at: int,
) -> Optional[Dict[str, Any]]:
    if event_type not in PLAN_EVENT_TYPES:
        return None
    plan_snapshot = plan_payload_from_event_payload(event_payload)
    if plan_snapshot is None:
        return None
    return {
        LATEST_PLAN_METADATA_KEY: sanitize_payload(plan_snapshot),
        LATEST_PLAN_EVENT_TYPE_METADATA_KEY: event_type,
        LATEST_PLAN_UPDATED_AT_METADATA_KEY: updated_at,
    }


def latest_plan_from_metadata(metadata: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(metadata, dict):
        return None
    latest_plan = metadata.get(LATEST_PLAN_METADATA_KEY)
    if isinstance(latest_plan, dict):
        return latest_plan
    return None


def plan_terminal_event_type(terminal_status: str) -> str:
    return f"plan_{terminal_status}"


def terminal_plan_payload(
    plan: Dict[str, Any],
    *,
    terminal_status: str,
    reason: str,
) -> Dict[str, Any]:
    payload = dict(plan)
    payload["planStatus"] = terminal_status
    payload["status"] = terminal_status
    payload["terminalReason"] = reason
    payload["eventType"] = plan_terminal_event_type(terminal_status)
    payload["command"] = terminal_status
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    statuses = list(payload.get("step_status") if isinstance(payload.get("step_status"), list) else [])
    statuses = [str(item or "not_started") for item in statuses]
    while len(statuses) < len(steps):
        statuses.append("not_started")
    statuses = statuses[: len(steps)]

    if terminal_status == "completed":
        payload["step_status"] = ["completed" for _ in steps]
        return payload

    if terminal_status in {"failed", "cancelled"} and steps:
        target_status = terminal_status
        if not any(status in {"running", "failed", "cancelled"} for status in statuses):
            for index, status in enumerate(statuses):
                if status != "completed":
                    statuses[index] = target_status
                    break
        else:
            statuses = [
                target_status if status == "running" else status
                for status in statuses
            ]
        payload["step_status"] = statuses
    return payload
