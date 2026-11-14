"""Runs one conversational turn through the supervisor graph.

New turn          -> ainvoke(fresh per-turn state)
Paused for a yes  -> ainvoke(Command(resume=...)) continues the same thread
"""

import logging
import time

from langgraph.types import Command

from src.services.college_client import college
from src.workflows.assistant_workflow import assistant_graph

log = logging.getLogger("assistant")

FRESH = {
    "context": None,
    "supervisor_decision": None,
    "service_result": None,
    "knowledge_result": None,
    "draft_reply": None,
    "evaluation": None,
    "pending_action": None,
    "confirmation": None,
    "awaiting_confirmation": False,
    "service_attempts": 0,
    "response_attempts": 0,
    "final_reply": None,
    "trace": [],
    "last_error": None,
    "workflow_status": "running",
}


async def run_turn(*, thread_id: str, text: str, role: str, user: dict | None, token: str | None, history: list) -> dict:
    t0 = time.perf_counter()
    try:
        today = (await college.today())["date"]
    except Exception:
        from datetime import date

        today = date.today().isoformat()
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    snapshot = await assistant_graph.aget_state(config)
    if snapshot.next and "human_confirmation" in snapshot.next:
        result = await assistant_graph.ainvoke(Command(resume={"text": text, "today": today, "history": history, "token": token}), config)
    else:
        state = {**FRESH, "user_query": text, "role": role, "user": user or {}, "token": token, "today": today, "history": history}
        result = await assistant_graph.ainvoke(state, config)

    waiting = bool(result.get("__interrupt__"))
    reply = result.get("final_reply") or result.get("draft_reply") or "Sorry, could you say that again?"
    pending = result.get("pending_action") if waiting else None
    ev = result.get("evaluation")
    return {
        "reply": reply,
        "status": "waiting_for_confirmation" if waiting else result.get("workflow_status", "completed"),
        "pending": {"question": pending["question"]} if pending else None,
        "trace": result.get("trace", []),
        "evaluation": {"score": ev.overall_score, "passed": ev.passed, "judged_by": ev.judged_by} if ev else None,
        "latency_ms": round((time.perf_counter() - t0) * 1000),
    }
