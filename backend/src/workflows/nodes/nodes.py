"""Graph nodes. Every worker reports back to the Supervisor (see graph edges)."""

import re
import time

from langgraph.types import interrupt

from src.agents.evaluation_agent import EvaluationAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.agents.response_agent import ResponseAgent, build_evidence, template_reply
from src.agents.service_agent import ServiceAgent
from src.agents.supervisor_agent import SupervisorAgent
from src.dtos.supervisor import SupervisorDecision
from src.workflows.routing.routing import decide_next
from src.workflows.state.assistant_state import AssistantState

supervisor_agent = SupervisorAgent()
service_agent = ServiceAgent()
knowledge_agent = KnowledgeAgent()
response_agent = ResponseAgent()
evaluation_agent = EvaluationAgent()

AFFIRM_RE = re.compile(r"^(yes|yeah|yep|yup|sure|ok(ay)?|confirm(ed)?|go ahead|do it|please do|submit( it)?|correct|right|absolutely|proceed|approve it|reject it|haan|ha)\b", re.I)
NEGATE_RE = re.compile(r"^(no|nope|nah|cancel|stop|don'?t|do not|wait|never ?mind)\b", re.I)


def classify_confirmation(text: str) -> str:
    t = re.sub(r"^(uh+|um+|so|well|oh)[, ]+", "", text.strip(), flags=re.I)
    if AFFIRM_RE.search(t) and len(t.split()) <= 8:
        return "yes"
    if NEGATE_RE.search(t) and len(t.split()) <= 6:
        return "no"
    return "new_query"


def _trace(state: AssistantState, agent: str, action: str, detail: str, t0: float) -> list:
    return list(state.get("trace") or []) + [{"agent": agent, "action": action, "detail": detail[:240], "ms": round((time.perf_counter() - t0) * 1000)}]


async def supervisor_node(state: AssistantState):
    t0 = time.perf_counter()
    updates: dict = {"current_agent": "supervisor"}
    trace = state.get("trace") or []
    if state.get("context") is None and state.get("confirmation") not in ("yes", "no"):
        ctx, engine = await supervisor_agent.understand(dict(state))
        updates["context"] = ctx
        trace = _trace({"trace": trace}, "supervisor", "understand", f"[{engine}] intent={ctx.intent} service={ctx.needs_service} knowledge={ctx.needs_knowledge} task={ctx.task}", t0)
        t0 = time.perf_counter()

    nxt, reason = decide_next({**state, **updates})
    updates["supervisor_decision"] = SupervisorDecision(next_agent=nxt, reason=reason)
    updates["next_agent"] = nxt
    if nxt == "finish":
        ev = state.get("evaluation")
        reply = state.get("draft_reply") or ""
        if ev is not None and ev.critical_issues:  # retries exhausted with a critical issue -> safe template
            reply = template_reply(state)
        updates["final_reply"] = reply
        updates["workflow_status"] = "completed"
    if nxt == "human_confirmation":
        updates["final_reply"] = state.get("draft_reply")
    updates["trace"] = _trace({"trace": trace}, "supervisor", f"route -> {nxt}", reason, t0)
    return updates


async def service_node(state: AssistantState):
    t0 = time.perf_counter()
    if state.get("confirmation") == "yes" and state.get("pending_action"):
        result = await service_agent.execute_pending(state["pending_action"], state.get("token"))
        return {
            "service_result": result,
            "pending_action": None,
            "current_agent": "service",
            "trace": _trace(state, "service", "execute staged action", f"{state['pending_action']['api']} -> {result.status}", t0),
        }
    attempts = state.get("service_attempts", 0) + 1
    result = await service_agent.run(dict(state))
    apis = ", ".join(c["api"] for c in result.calls if "api" in c) or "-"
    return {
        "service_result": result,
        "service_attempts": attempts,
        "pending_action": result.pending,
        "last_error": (result.error or {}).get("message"),
        "current_agent": "service",
        "trace": _trace(state, "service", f"attempt {attempts}: {result.status}", f"apis: {apis}. {result.message}", t0),
    }


async def knowledge_node(state: AssistantState):
    t0 = time.perf_counter()
    ctx = state["context"]
    programme = (state.get("user") or {}).get("programme") or "UG"
    passages = knowledge_agent.run(ctx.knowledge_query or ctx.task, ctx.intent, state.get("role", "visitor"), programme)
    return {
        "knowledge_result": passages,
        "current_agent": "knowledge",
        "trace": _trace(state, "knowledge", f"{len(passages)} passages", "; ".join(p["source"] for p in passages), t0),
    }


async def response_node(state: AssistantState):
    t0 = time.perf_counter()
    reply, engine = await response_agent.run(dict(state))
    attempts = state.get("response_attempts", 0) + 1
    return {
        "draft_reply": reply,
        "evaluation": None,  # every new draft must be evaluated again
        "response_attempts": attempts,
        "current_agent": "response",
        "trace": _trace(state, "response", f"draft {attempts} [{engine}]", reply, t0),
    }


async def evaluation_node(state: AssistantState):
    t0 = time.perf_counter()
    ev = await evaluation_agent.run(state.get("draft_reply") or "", dict(state), build_evidence(dict(state)))
    issues = "; ".join(ev.critical_issues + ev.revision_instructions) or "ok"
    return {
        "evaluation": ev,
        "current_agent": "evaluation",
        "trace": _trace(state, "evaluation", f"{'PASS' if ev.passed else 'FAIL'} {ev.overall_score} [{ev.judged_by}]", issues, t0),
    }


def human_confirmation_node(state: AssistantState):
    """Pause until the user answers. Resumed with Command(resume={...})."""
    answer = interrupt({"reply": state.get("final_reply"), "question": (state.get("pending_action") or {}).get("question")})
    text = str(answer.get("text", ""))
    decision = classify_confirmation(text)
    base = {
        "user_query": text,
        "today": answer.get("today", state.get("today")),
        "history": answer.get("history", state.get("history")),
        "token": answer.get("token", state.get("token")),
        "awaiting_confirmation": False,
        "draft_reply": None,
        "evaluation": None,
        "final_reply": None,
        "response_attempts": 0,
        "service_attempts": 0,
        "knowledge_result": None,
        "last_error": None,
        "workflow_status": "running",
        "current_agent": "human_confirmation",
        "trace": [{"agent": "human_confirmation", "action": f"user answered: {decision}", "detail": text, "ms": 0}],
    }
    if decision == "yes":
        return {**base, "confirmation": "yes", "service_result": None}
    if decision == "no":
        return {**base, "confirmation": "no", "pending_action": None, "service_result": None}
    # Anything else is a new request: drop the staged action and start over.
    return {**base, "confirmation": None, "pending_action": None, "service_result": None, "context": None}
