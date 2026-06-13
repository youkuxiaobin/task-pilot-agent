from __future__ import annotations

from brain.core.plan_snapshots import (
    LATEST_PLAN_EVENT_TYPE_METADATA_KEY,
    LATEST_PLAN_METADATA_KEY,
    LATEST_PLAN_UPDATED_AT_METADATA_KEY,
    latest_plan_from_metadata,
    latest_plan_metadata_fields,
    plan_payload_from_event_payload,
    plan_terminal_event_type,
    terminal_plan_payload,
)


def test_plan_payload_from_event_payload_supports_runtime_shapes():
    nested_plan = {"title": "Nested", "steps": ["Search"]}
    result_map_plan = {"title": "Result map", "steps": ["Read"]}
    direct_plan = {"title": "Direct", "steps": ["Summarize"]}

    assert plan_payload_from_event_payload({"plan": nested_plan}) == nested_plan
    assert plan_payload_from_event_payload({"resultMap": {"plan": result_map_plan}}) == result_map_plan
    assert plan_payload_from_event_payload(direct_plan) == direct_plan
    assert plan_payload_from_event_payload({"message": "not a plan"}) is None
    assert plan_payload_from_event_payload("not a payload") is None


def test_latest_plan_metadata_fields_sanitizes_and_filters_non_plan_events():
    fields = latest_plan_metadata_fields(
        "plan_step_completed",
        {
            "plan": {
                "title": "Research",
                "steps": ["Search"],
                "step_status": ["completed"],
                "evidence": [[{"url": "https://example.test", "api_key": "hidden"}]],
            }
        },
        1234,
    )

    assert fields is not None
    assert fields[LATEST_PLAN_EVENT_TYPE_METADATA_KEY] == "plan_step_completed"
    assert fields[LATEST_PLAN_UPDATED_AT_METADATA_KEY] == 1234
    assert fields[LATEST_PLAN_METADATA_KEY]["evidence"][0][0]["url"] == "https://example.test"
    assert fields[LATEST_PLAN_METADATA_KEY]["evidence"][0][0]["api_key"] == "***"
    assert latest_plan_from_metadata(fields) == fields[LATEST_PLAN_METADATA_KEY]
    assert latest_plan_metadata_fields("tool_call", {"plan": {"steps": ["Search"]}}, 1234) is None
    assert latest_plan_from_metadata({"latestPlan": "not a dict"}) is None


def test_terminal_plan_payload_marks_steps_for_terminal_statuses():
    plan = {
        "title": "Demo",
        "steps": ["Search", "Write"],
        "step_status": ["running", "not_started"],
    }

    completed = terminal_plan_payload(plan, terminal_status="completed", reason="done")
    failed = terminal_plan_payload(plan, terminal_status="failed", reason="boom")
    cancelled = terminal_plan_payload(plan, terminal_status="cancelled", reason="stop")

    assert plan_terminal_event_type("failed") == "plan_failed"
    assert completed["eventType"] == "plan_completed"
    assert completed["step_status"] == ["completed", "completed"]
    assert failed["eventType"] == "plan_failed"
    assert failed["step_status"] == ["failed", "not_started"]
    assert failed["terminalReason"] == "boom"
    assert cancelled["eventType"] == "plan_cancelled"
    assert cancelled["step_status"] == ["cancelled", "not_started"]
