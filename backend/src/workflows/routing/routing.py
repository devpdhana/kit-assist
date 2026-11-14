"""Deterministic routing guards for the Supervisor.

The Supervisor's LLM decides WHAT the user needs (QueryContext). These guards
decide the ORDER, and guarantee that safety steps (evaluation, confirmation,
retry limits) can never be skipped - mirroring the content-creator workflow.
"""

from langgraph.graph import END

from src.configs.config import settings

WORKER_ROUTES = {"service", "knowledge", "response", "evaluation", "human_confirmation"}


def decide_next(state: dict) -> tuple[str, str]:
    """Pure function: (next_agent, reason)."""
    ctx = state.get("context")
    sr = state.get("service_result")
    ev = state.get("evaluation")
    draft = state.get("draft_reply")
    confirmation = state.get("confirmation")

    # 1. Human answered a staged action.
    if confirmation == "yes" and state.get("pending_action") and sr is None:
        return "service", "user confirmed the staged action - execute it"
    if confirmation == "no":
        return ("response", "user declined - acknowledge the cancellation") if not draft else ("finish", "cancellation acknowledged")

    # 2. Gather evidence.
    if ctx is not None and ctx.needs_service and sr is None:
        return "service", f"intent '{ctx.intent}' needs college system data/actions"
    if sr is not None and sr.status == "error":
        retryable = (sr.error or {}).get("retryable", False)
        if retryable and state.get("service_attempts", 0) <= settings.MAX_SERVICE_RETRIES:
            return "service", f"service failed ({(sr.error or {}).get('message', 'error')}) - retrying"
    if ctx is not None and ctx.needs_knowledge and state.get("knowledge_result") is None:
        return "knowledge", "needs official documents"

    # 3. Compose and evaluate.
    if not draft:
        return "response", "evidence ready - compose the reply"
    if ev is None:
        return "evaluation", "every reply is evaluated before it is sent"
    if not ev.passed and state.get("response_attempts", 0) <= settings.MAX_RESPONSE_RETRIES:
        return "response", f"evaluation failed ({ev.overall_score}) - revise"

    # 4. Pause for the human if an action is staged.
    if sr is not None and sr.status == "staged" and not state.get("awaiting_confirmation"):
        return "human_confirmation", "write action staged - wait for the user's yes/no"

    return "finish", "reply passed evaluation"


def route_from_supervisor(state: dict) -> str:
    nxt = state.get("next_agent")
    return nxt if nxt in WORKER_ROUTES else END
