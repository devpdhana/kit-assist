from typing import Any, Optional, TypedDict


class AssistantState(TypedDict, total=False):
    # ---- who / when (set per turn by the API layer) ----
    user_query: str
    role: str  # visitor | student | teacher
    user: dict  # public profile of the logged-in user (empty for visitors)
    token: Optional[str]  # college bearer token - lives only in server memory
    today: str  # college date (YYYY-MM-DD) from /v1/today
    history: list  # last few {role, content} turns

    # ---- supervisor ----
    context: Any  # QueryContext
    supervisor_decision: Any  # SupervisorDecision
    next_agent: str
    current_agent: str

    # ---- worker outputs ----
    service_result: Any  # ServiceResult
    knowledge_result: Any  # list of passages
    draft_reply: Optional[str]
    evaluation: Any  # ResponseEvaluation

    # ---- human-in-the-loop confirmation ----
    pending_action: Optional[dict]  # staged write
    confirmation: Optional[str]  # yes | no | new_query
    awaiting_confirmation: bool

    # ---- retry management ----
    service_attempts: int
    response_attempts: int

    # ---- output / observability ----
    final_reply: Optional[str]
    workflow_status: str  # running | waiting_for_confirmation | completed | failed
    trace: list  # [{agent, action, detail, ms}]
    last_error: Optional[str]
