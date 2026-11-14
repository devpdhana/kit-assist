"""Supervisor Agent: understands the query in context (one LLM call per turn)
and decides which agent runs next. Routing after the first step is
deterministic (see workflows/routing) so the supervisor stays fast and can
never skip a safety step."""

import logging
import re
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from src.configs.config import settings
from src.dtos.supervisor import QueryContext
from src.services import llm
from src.services.dates import date_table

log = logging.getLogger("supervisor")

HUMAN = """Role: {role}{who}
College date today: {today}. Calendar: {dates}
Conversation so far:
{history}

User says: "{query}"

Classify this query and write the self-contained task."""


class SupervisorAgent:
    def __init__(self) -> None:
        self.system_prompt = (Path(__file__).resolve().parents[1] / "prompts" / "supervisor.md").read_text(encoding="utf-8")
        self.prompt = ChatPromptTemplate.from_messages([("system", self.system_prompt), ("human", HUMAN)])

    async def understand(self, state: dict) -> tuple[QueryContext, str]:
        """Returns (context, engine) where engine is 'llm' or 'rules'."""
        if settings.has_llm:
            try:
                chain = self.prompt | llm.structured(QueryContext, tier="fast")
                ctx = await chain.ainvoke(self._inputs(state))
                return self._guard(ctx, state), "llm"
            except Exception as exc:  # every provider failed -> rule-based understanding
                log.warning("supervisor LLM failed: %s", str(exc)[:200])
        return self._guard(classify_by_rules(state["user_query"], state.get("role", "visitor")), state), "rules"

    @staticmethod
    def _inputs(state: dict) -> dict:
        user = state.get("user") or {}
        who = f" ({user.get('full_name')}, {user.get('class_code') or user.get('department')})" if user else ""
        hist = "\n".join(f"{h['role']}: {h['content'][:300]}" for h in (state.get("history") or [])[-6:]) or "(none)"
        return {
            "role": state.get("role", "visitor"),
            "who": who,
            "today": state["today"],
            "dates": date_table(state["today"]),
            "history": hist,
            "query": state["user_query"],
        }

    @staticmethod
    def _guard(ctx: QueryContext, state: dict) -> QueryContext:
        # Visitors never reach personal-data APIs, whatever the model decided.
        if state.get("role") == "visitor" and ctx.intent == "account_data":
            ctx.intent, ctx.needs_service = "out_of_scope", False
        if ctx.needs_knowledge and not ctx.knowledge_query:
            ctx.knowledge_query = ctx.task or state["user_query"]
        if not ctx.task:
            ctx.task = state["user_query"]
        return ctx


# ---------------- rule-based fallback (no LLM available) ----------------

_ACTION = re.compile(r"\b(apply|submit|request|take|need|want|cancel|register|approve|reject)\b.*\b(leave|on.?duty|od|request|drive|placement|application|admission)\b|\b(approve|reject)\b", re.I)
_DATA = re.compile(r"\b(my|pending|status|attendance|timetable|schedule|class(es)?|calendar|holiday|exam|internal|request)\b", re.I)
_RULES = re.compile(r"\b(rule|policy|allowed|can i|deadline|fee|fees|scholarship|document|circular|minimum|maximum|how many|condonation|hostel)\b", re.I)
_LESSON = re.compile(r"\b(bcnf|normal form|normalisation|normalization|sql|join|er model|relational|dbms|transaction|functional dependenc)", re.I)
_ADMISSION = re.compile(r"\b(course|eligib|seat|branch|admission|cutoff|cut.off|join)\b", re.I)
_DOCLIST = re.compile(r"\b(what|which|any|list|show)\b.*\b(circulars|documents|announcements|handbook (edition|applies)|notes available)\b|\bwhich handbook\b|\bnew (circular|announcement)s?\b", re.I)
_SMALL = re.compile(r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|what can you do)\b", re.I)


def classify_by_rules(query: str, role: str) -> QueryContext:
    q = query.strip()
    if _SMALL.search(q):
        return QueryContext(intent="small_talk", needs_service=False, needs_knowledge=False, task=q, reason="greeting (rules)")
    if _DOCLIST.search(q):
        return QueryContext(intent="rules_or_documents", needs_service=True, needs_knowledge=False, task=q, reason="document listing (rules)")
    if _LESSON.search(q):
        return QueryContext(intent="lesson_doubt", needs_service=False, needs_knowledge=True, task=q, knowledge_query=q, reason="subject keywords (rules)")
    if _ACTION.search(q) and not re.search(r"\b(how many|what is|deadline|rule)\b", q, re.I):
        return QueryContext(intent="action_request", needs_service=True, needs_knowledge=False, task=q, reason="action keywords (rules)")
    if role != "visitor" and _DATA.search(q) and not _RULES.search(q):
        return QueryContext(intent="account_data", needs_service=True, needs_knowledge=False, task=q, reason="personal data keywords (rules)")
    if _ADMISSION.search(q) and not _RULES.search(q):
        return QueryContext(intent="admissions_info", needs_service=True, needs_knowledge=False, task=q, reason="admission keywords (rules)")
    return QueryContext(intent="rules_or_documents", needs_service=False, needs_knowledge=True, task=q, knowledge_query=q, reason="default to documents (rules)")
