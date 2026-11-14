"""Response Agent: turns evidence into a short, spoken-style reply.
Falls back to deterministic templates when no LLM is available."""

import json
import logging
import re
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from src.configs.config import settings
from src.services import llm
from src.services.policy import pretty

log = logging.getLogger("response")

HUMAN = """Role: {role}{who}. College date today: {today}.
Recent conversation:
{history}

User said: "{query}"
Understood task: {task}

EVIDENCE
{evidence}
{feedback}
Write the spoken reply now."""


def build_evidence(state: dict) -> str:
    parts: list[str] = []
    sr = state.get("service_result")
    if sr is not None:
        parts.append(f"Service status: {sr.status.upper()}")
        if sr.message:
            parts.append(f"Service message: {sr.message}")
        if sr.policy:
            parts.append("Rulebook check: " + json.dumps(sr.policy, default=str)[:1200])
        for c in sr.calls:
            if "api" not in c:
                continue
            body = c.get("data") if c.get("ok") else {"error": c.get("error")}
            parts.append(f"[{c['api']}] " + json.dumps(body, default=str)[:2000])
        if sr.error:
            parts.append(f"Error: {sr.error.get('message')}")
    for p in state.get("knowledge_result") or []:
        parts.append(f"[{p['source']} | {p['label']}]\n{p['text']}")
    if state.get("confirmation") == "no":
        parts.append("The user declined the staged action; it was cancelled.")
    return "\n".join(parts) or "(no evidence - small talk)"


class ResponseAgent:
    def __init__(self) -> None:
        system = (Path(__file__).resolve().parents[1] / "prompts" / "response.md").read_text(encoding="utf-8")
        self.prompt = ChatPromptTemplate.from_messages([("system", system), ("human", HUMAN)])

    async def run(self, state: dict) -> tuple[str, str]:
        if state.get("confirmation") == "no":
            return "Okay, I've cancelled that. Anything else?", "template"
        sr = state.get("service_result")
        if sr is not None and sr.status == "executed" and not state.get("evaluation"):
            return sr.message, "template"  # exact confirmation from the system, no paraphrase risk
        ctx = state.get("context")
        asks_student_data = re.search(r"attendance|marks|request|student", state["user_query"], re.I)
        if ctx is not None and ctx.intent == "out_of_scope" and state.get("role") == "visitor" and asks_student_data:
            return "Sorry, I can't share any student information here. Students can log in to see their own details.", "template"
        if settings.has_llm:
            try:
                ev = state.get("evaluation")
                feedback = ""
                if ev is not None and not ev.passed:
                    feedback = "\nYOUR PREVIOUS REPLY FAILED REVIEW. Fix: " + "; ".join(ev.critical_issues + ev.revision_instructions)
                user = state.get("user") or {}
                chain = self.prompt | llm.chat(tier="smart")
                msg = await chain.ainvoke({
                    "role": state.get("role", "visitor"),
                    "who": f" ({user.get('full_name')})" if user else "",
                    "today": pretty(state["today"]),
                    "history": "\n".join(f"{h['role']}: {h['content'][:200]}" for h in (state.get("history") or [])[-4:]) or "(none)",
                    "query": state["user_query"],
                    "task": ctx.task if ctx else state["user_query"],
                    "evidence": build_evidence(state),
                    "feedback": feedback,
                })
                text = clean_reply(msg.content if isinstance(msg.content, str) else str(msg.content))
                if text:
                    return text, "llm"
            except Exception as exc:
                log.warning("response LLM failed: %s", str(exc)[:200])
        return template_reply(state), "template"


def clean_reply(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"(^|\n)\s*(?:[-•]|\d+\.)\s+", r"\1", text)  # list markers don't belong in speech
    return re.sub(r"\s*\n\s*", " ", text).strip()


def template_reply(state: dict) -> str:
    """Deterministic reply used when every LLM is down."""
    sr = state.get("service_result")
    ctx = state.get("context")
    if ctx is not None and ctx.intent == "small_talk":
        return "Hello! I'm KIT Assist. Ask me about attendance, timetable, leave, rules, courses or admissions."
    if sr is not None:
        if sr.status == "staged":
            return f"{sr.message} Shall I go ahead?"
        if sr.status in ("blocked", "needs_info", "executed"):
            return sr.message
        if sr.status == "error":
            return "Sorry, the college system didn't respond. Please try again in a moment."
        for c in sr.calls:
            if c.get("ok"):
                s = _summarize(c["api"], c["data"])
                if s:
                    return s
    for p in state.get("knowledge_result") or []:
        if p["binding"]:
            snippet = " ".join(re.split(r"(?<=[.])\s+", p["text"])[:2])[:380]
            return f"According to the {p['source']}: {snippet}"
    return "I'm in limited mode right now and couldn't work that out. You can call the office at +91 422 2619000."


def _summarize(api: str, data) -> str:
    if api == "my_attendance" and isinstance(data, dict):
        subs = "; ".join(f"{s['subject_name']} {s['percentage']}%" for s in data.get("subjects", []))
        return f"Your overall attendance is {data.get('overall_percentage')}%. {subs}."
    if api == "my_requests" and isinstance(data, list):
        if not data:
            return "You have no leave or on-duty requests."
        return ". ".join(f"Request {r['id']}, {r['type'].replace('_', ' ')} on {pretty(r['from_date'])}, is {r['status']}" for r in data[:4]) + "."
    if api == "teacher_requests" and isinstance(data, list):
        if not data:
            return "You have no pending requests."
        return f"You have {len(data)} requests: " + "; ".join(f"number {r['id']}, {r['student_name']}, {r['type'].replace('_', ' ')} on {pretty(r['from_date'])}" for r in data[:4]) + "."
    if api == "list_courses" and isinstance(data, list):
        return ". ".join(f"{c['name']}: minimum {c['min_12th_percent']}% in 12th, {int(c['annual_fee'])} rupees a year, {c['seats']} seats" for c in data) + "."
    if api == "calendar" and isinstance(data, list):
        return ("Coming up: " + "; ".join(f"{e['title']} on {pretty(e['date'])}" for e in data[:5]) + ".") if data else "No holidays or exams in that period."
    if api == "placement_drives" and isinstance(data, list):
        return "Active drives: " + "; ".join(f"{d['company']}, minimum CGPA {d['min_cgpa']}, deadline {pretty(d['deadline'])}" for d in data) + "."
    if api == "list_documents" and isinstance(data, list):
        if not data:
            return "I couldn't find any documents of that kind."
        return "Available documents: " + "; ".join(
            f"{d['title']} ({d['status']}{', new' if d['is_new'] else ''})" for d in data
        ) + "."
    if api == "my_timetable" and isinstance(data, list):
        return f"You have {len(data)} periods a week. Ask me about a specific day for details."
    return ""
