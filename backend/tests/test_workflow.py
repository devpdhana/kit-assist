"""Supervisor routing guards, evaluation metrics, role scoping and confirmation handling."""

from src.agents.evaluation_agent import EvaluationAgent
from src.agents.response_agent import clean_reply
from src.agents.supervisor_agent import classify_by_rules
from src.dtos.evaluation import ResponseEvaluation
from src.dtos.service import ServiceResult
from src.dtos.supervisor import QueryContext
from src.kb.api_catalog import allowed, apis_for
from src.services.dates import extract_dates
from src.services.sessions import unsign
from src.services.stt import clean_transcript
from src.workflows.nodes.nodes import classify_confirmation
from src.workflows.routing.routing import decide_next

CTX_SERVICE = QueryContext(intent="account_data", needs_service=True, needs_knowledge=False, task="attendance")
CTX_BOTH = QueryContext(intent="rules_or_documents", needs_service=True, needs_knowledge=True, task="x", knowledge_query="x")
PASS = ResponseEvaluation(overall_score=100, passed=True, criteria=[])
FAIL = ResponseEvaluation(overall_score=50, passed=False, criteria=[], critical_issues=["x"])


# ---------------- supervisor routing ----------------
def test_supervisor_routes_service_then_knowledge_then_response_then_evaluation():
    assert decide_next({"context": CTX_BOTH})[0] == "service"
    s = {"context": CTX_BOTH, "service_result": ServiceResult(status="ok")}
    assert decide_next(s)[0] == "knowledge"
    s["knowledge_result"] = []
    assert decide_next(s)[0] == "response"
    s["draft_reply"] = "hi there friend"
    assert decide_next(s)[0] == "evaluation"
    s["evaluation"] = PASS
    assert decide_next(s)[0] == "finish"


def test_service_error_retried_with_limit():
    err = ServiceResult(status="error", error={"message": "timeout", "retryable": True})
    assert decide_next({"context": CTX_SERVICE, "service_result": err, "service_attempts": 1})[0] == "service"
    assert decide_next({"context": CTX_SERVICE, "service_result": err, "service_attempts": 3})[0] == "response"
    fatal = ServiceResult(status="error", error={"message": "bad input", "retryable": False})
    assert decide_next({"context": CTX_SERVICE, "service_result": fatal, "service_attempts": 1})[0] == "response"


def test_failed_evaluation_revises_then_gives_up():
    base = {"context": CTX_SERVICE, "service_result": ServiceResult(status="ok"), "draft_reply": "x", "evaluation": FAIL}
    assert decide_next({**base, "response_attempts": 1})[0] == "response"
    assert decide_next({**base, "response_attempts": 3})[0] == "finish"


def test_staged_action_pauses_for_human_and_yes_executes():
    staged = ServiceResult(status="staged", pending={"api": "x"})
    s = {"context": CTX_SERVICE, "service_result": staged, "draft_reply": "Submit? ", "evaluation": PASS}
    assert decide_next(s)[0] == "human_confirmation"
    assert decide_next({"context": CTX_SERVICE, "confirmation": "yes", "pending_action": {"api": "x"}})[0] == "service"
    assert decide_next({"context": CTX_SERVICE, "confirmation": "no"})[0] == "response"


def test_confirmation_classifier():
    for y in ["yes", "Yes please", "okay go ahead", "um, yes", "confirm"]:
        assert classify_confirmation(y) == "yes", y
    for n in ["no", "cancel it", "don't", "wait"]:
        assert classify_confirmation(n) == "no", n
    assert classify_confirmation("what is my attendance") == "new_query"


# ---------------- role scoping ----------------
def test_visitors_cannot_reach_student_or_teacher_apis():
    ids = {a.id for a in apis_for("visitor")}
    assert not ids & {"my_attendance", "my_requests", "submit_leave_request", "approve_request"}
    assert "submit_admission_application" in ids
    assert not allowed("student", "approve_request") and not allowed("teacher", "submit_leave_request")


def test_rule_based_supervisor_never_gives_visitor_account_data():
    assert classify_by_rules("show my attendance", "visitor").intent != "account_data"
    assert classify_by_rules("show my attendance", "student").intent == "account_data"
    assert classify_by_rules("explain BCNF", "student").intent == "lesson_doubt"


# ---------------- evaluation metrics ----------------
def _state(sr=None):
    return {"user_query": "q", "today": "2026-11-21", "service_result": sr}


def test_evaluation_flags_ungrounded_numbers():
    ev = EvaluationAgent().score("Your attendance is 88%.", _state(), '{"percentage": 76.0}')
    assert not ev.passed
    ok = EvaluationAgent().score("Your attendance is 76%.", _state(), '{"percentage": 76.0}')
    assert ok.passed


def test_evaluation_blocks_false_submission_claims():
    staged = ServiceResult(status="staged", pending={"api": "submit_leave_request"})
    ev = EvaluationAgent().score("Your leave has been submitted.", _state(staged), "")
    assert not ev.passed and ev.critical_issues


def test_evaluation_blocks_secret_leaks():
    ev = EvaluationAgent().score("Here you go kit_live_abc123", _state(), "")
    assert any("secret" in c for c in ev.critical_issues)


# ---------------- misc ----------------
def test_relative_dates_use_college_date():
    assert extract_dates("leave tomorrow", "2026-11-21") == ["2026-11-22"]
    assert extract_dates("leave on monday", "2026-11-21") == ["2026-11-23"]
    assert extract_dates("on 30th november", "2026-11-21") == ["2026-11-30"]


def test_reply_and_transcript_cleaning():
    assert clean_reply("**Hi** there `x`") == "Hi there x"
    assert clean_transcript("Thank you.") == ""


def test_forged_session_cookie_rejected():
    assert unsign("abc.def") is None and unsign(None) is None
