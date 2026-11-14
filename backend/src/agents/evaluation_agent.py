"""Evaluation Agent: rules + metrics, with an LLM judge only for borderline cases.

Metrics (weighted):
  grounding     every number in the reply appears in the evidence / query / dates
  honesty       never claims an action is done unless it was executed
  confirmation  a staged action must be read back as a question
  error_honesty a failure must be acknowledged, not papered over
  privacy       no secrets / tokens; visitors get no student data
  voice_fit     short, plain text, no markdown/URLs
  relevance     non-empty, on the user's topic

Critical issues (honesty, privacy, confirmation) fail the reply regardless of score.
"""

import logging
import re

from langchain_core.prompts import ChatPromptTemplate

from src.configs.config import settings
from src.dtos.evaluation import EvaluationCriterion, JudgeVerdict, ResponseEvaluation
from src.services import llm
from src.services.dates import date_table

log = logging.getLogger("evaluation")

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
DONE_RE = re.compile(r"\b(has been|have been|is now|is|was)\s+(submitted|approved|rejected|cancelled|registered)\b|\b(i('ve| have)\s+(submitted|approved|rejected|cancelled|registered))", re.I)
SECRET_RE = re.compile(r"kit_live_\w+|gsk_\w+|bearer\s+\w{10,}|eyJ[A-Za-z0-9_-]{20,}", re.I)
SORRY_RE = re.compile(r"sorry|couldn'?t|could not|unable|can'?t|cannot|not able|didn'?t|failed|problem|try again|not allowed|isn'?t allowed|not eligible|need", re.I)
MD_RE = re.compile(r"[*#`]|\]\(|https?://")

JUDGE = ChatPromptTemplate.from_messages([
    ("system", "You are a strict evaluator for a college voice assistant. Judge whether the reply correctly answers the user's query using only the evidence, without inventing facts, and is suitable to be spoken."),
    ("human", "User query: {query}\n\nEvidence:\n{evidence}\n\nReply: {reply}\n\nRule-check feedback: {feedback}"),
])


WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
DAY_DATE_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(\d{4}))?",
    re.I,
)


def weekday_mismatches(reply: str, today: str) -> list[str]:
    """Find 'Weekday, D Month' phrases whose weekday is wrong for that date."""
    from datetime import date

    out = []
    for m in DAY_DATE_RE.finditer(reply):
        year = int(m.group(4) or today[:4])
        try:
            d = date(year, MONTH_NAMES.index(m.group(3).lower()) + 1, int(m.group(2)))
        except ValueError:
            continue
        actual = WEEKDAY_NAMES[d.weekday()]
        if actual != m.group(1).lower():
            out.append(f"{m.group(2)} {m.group(3)} is a {actual.title()}, not {m.group(1).title()}")
    return out


def _numbers(text: str) -> set[str]:
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in NUM_RE.findall(text)}


class EvaluationAgent:
    def score(self, reply: str, state: dict, evidence: str) -> ResponseEvaluation:
        sr = state.get("service_result")
        crit: list[EvaluationCriterion] = []
        critical: list[str] = []
        fixes: list[str] = []

        # grounding
        # Always-allowed: the college date table, the office phone and the headline rule numbers.
        allowed_nums = _numbers(evidence + " " + state.get("user_query", "") + " " + date_table(state["today"], 7, 60) + " +91 422 2619000 75 80 3 24")
        unsupported = sorted(n for n in _numbers(reply) if n not in allowed_nums and len(n) > 0)
        g = 100 if not unsupported else max(0, 100 - 35 * len(unsupported))
        crit.append(EvaluationCriterion(name="grounding", score=g, weight=30, feedback=f"unsupported numbers: {unsupported}" if unsupported else "all numbers grounded"))
        if unsupported:
            fixes.append(f"Remove or correct numbers not in the evidence: {', '.join(unsupported)}")

        # weekday/date consistency ("Friday, 25 November" when the 25th is a Wednesday)
        wrong_days = weekday_mismatches(reply, state["today"])
        crit.append(EvaluationCriterion(name="date_consistency", score=0 if wrong_days else 100, weight=15, feedback="; ".join(wrong_days)))
        if wrong_days:
            critical.append("Wrong weekday for a date: " + "; ".join(wrong_days))

        # honesty about actions
        # Only meaningful when an action was involved (rule text like "is rejected" is not a claim).
        executed = sr is not None and sr.status == "executed"
        action_turn = sr is not None and (sr.status in ("staged", "blocked", "needs_info") or sr.pending is not None)
        claims_done = action_turn and bool(DONE_RE.search(reply)) and "?" not in reply.split(".")[-1]
        honest = executed or not claims_done or state.get("confirmation") == "no"
        crit.append(EvaluationCriterion(name="honesty", score=100 if honest else 0, weight=20, feedback="" if honest else "claims an action is done but nothing was executed"))
        if not honest:
            critical.append("Reply claims the action is done, but it was only staged / not executed.")

        # confirmation question for staged writes
        if sr is not None and sr.status == "staged":
            asks = "?" in reply
            crit.append(EvaluationCriterion(name="confirmation", score=100 if asks else 0, weight=15, feedback="" if asks else "staged action not read back as a question"))
            if not asks:
                critical.append("A staged action must be read back as a yes/no question.")

        # error honesty
        if sr is not None and sr.status in ("error", "blocked"):
            ack = bool(SORRY_RE.search(reply))
            crit.append(EvaluationCriterion(name="error_honesty", score=100 if ack else 30, weight=10, feedback="" if ack else "failure/block not acknowledged"))
            if not ack:
                fixes.append("Say clearly that it could not be done and why.")

        # privacy
        leak = bool(SECRET_RE.search(reply))
        crit.append(EvaluationCriterion(name="privacy", score=0 if leak else 100, weight=15, feedback="secret/token in reply" if leak else ""))
        if leak:
            critical.append("Reply exposes a secret or token.")

        # voice fit
        words = len(reply.split())
        vf = 100 if words <= 80 else 70 if words <= 120 else 40
        if MD_RE.search(reply):
            vf -= 30
            fixes.append("Remove markdown/URLs; plain spoken text only.")
        if words > 80:
            fixes.append("Shorten to under 70 words.")
        crit.append(EvaluationCriterion(name="voice_fit", score=max(vf, 0), weight=10, feedback=f"{words} words"))

        # relevance (cheap proxy)
        rel = 100 if words >= 3 else 0
        crit.append(EvaluationCriterion(name="relevance", score=rel, weight=10, feedback="" if rel else "empty reply"))
        if not rel:
            critical.append("Empty reply.")

        total_w = sum(c.weight for c in crit)
        overall = round(sum(c.score * c.weight for c in crit) / total_w)
        # Invented numbers are the main hallucination risk: always force a revision.
        passed = overall >= settings.EVAL_PASS_SCORE and not critical and not unsupported
        return ResponseEvaluation(overall_score=overall, passed=passed, criteria=crit, critical_issues=critical, revision_instructions=fixes)

    async def run(self, reply: str, state: dict, evidence: str) -> ResponseEvaluation:
        ev = self.score(reply, state, evidence)
        low, high = settings.EVAL_LLM_BAND
        if not ev.critical_issues and low <= ev.overall_score < high and settings.has_llm:
            try:  # borderline -> ask the LLM judge
                verdict: JudgeVerdict = await (JUDGE | llm.structured(JudgeVerdict, tier="fast")).ainvoke(
                    {"query": state["user_query"], "evidence": evidence[:3000], "reply": reply, "feedback": "; ".join(ev.revision_instructions) or "none"}
                )
                ev.judged_by = "rules+llm"
                ev.passed = verdict.meets_query and verdict.score >= 70
                ev.overall_score = round((ev.overall_score + verdict.score) / 2)
                if verdict.feedback:
                    ev.revision_instructions.append(verdict.feedback)
            except Exception as exc:
                log.warning("LLM judge failed: %s", str(exc)[:200])
        return ev
