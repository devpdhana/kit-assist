"""Service Agent: its knowledge base is the college API catalog.

plan    -> LLM picks catalog APIs + args for the task (rule planner if no LLM)
guard   -> drops APIs the role may not call; at most one write
execute -> reads run in parallel; writes are validated, run through the
           Policy Guard and STAGED (never sent) until the user confirms
confirm -> executes the staged write with its Idempotency-Key
"""

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from src.configs.config import settings
from src.dtos.service import ApiArg, ApiCall, ServicePlan, ServiceResult
from src.kb.api_catalog import CATALOG, ApiSpec, allowed, catalog_prompt
from src.services import llm
from src.services.college_client import CollegeError, college
from src.services.dates import date_table, extract_dates
from src.services.policy import add_days, can_decide, evaluate_request, pretty

log = logging.getLogger("service")

HUMAN = """API catalog for role "{role}":
{catalog}

User: {who}
College date today: {today}. Calendar: {dates}
Recent conversation:
{history}

Task: {task}
{previous_error}
Return the plan."""

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
INT_ARGS = {"id", "class_id"}


class ServiceAgent:
    def __init__(self) -> None:
        self.system_prompt = (Path(__file__).resolve().parents[1] / "prompts" / "service.md").read_text(encoding="utf-8")
        self.prompt = ChatPromptTemplate.from_messages([("system", self.system_prompt), ("human", HUMAN)])

    # ------------------------------------------------------------------ plan
    async def plan(self, state: dict) -> tuple[ServicePlan, str]:
        role = state.get("role", "visitor")
        if settings.has_llm:
            try:
                user = state.get("user") or {}
                chain = self.prompt | llm.structured(ServicePlan, tier="fast")
                plan = await chain.ainvoke({
                    "role": role,
                    "catalog": catalog_prompt(role),
                    "who": f"{user.get('full_name')} ({user.get('role')}, {user.get('class_code') or user.get('department')}, year {user.get('year_of_study')})" if user else "anonymous visitor",
                    "today": state["today"],
                    "dates": date_table(state["today"]),
                    "history": "\n".join(f"{h['role']}: {h['content'][:200]}" for h in (state.get("history") or [])[-4:]) or "(none)",
                    "task": state["context"].task,
                    "previous_error": f"Previous attempt failed: {state['last_error']}" if state.get("last_error") else "",
                })
                return plan, "llm"
            except Exception as exc:
                log.warning("service planner LLM failed: %s", str(exc)[:200])
        return plan_by_rules(state), "rules"

    # --------------------------------------------------------------- execute
    async def run(self, state: dict) -> ServiceResult:
        plan, engine = await self.plan(state)
        role, token = state.get("role", "visitor"), state.get("token")

        calls = [c for c in plan.calls if allowed(role, c.api)]  # server-side role scoping
        dropped = [c.api for c in plan.calls if not allowed(role, c.api)]
        writes = [c for c in calls if CATALOG[c.api].kind == "write"][:1]
        reads = [c for c in calls if CATALOG[c.api].kind == "read"]

        if plan.missing_info and not writes:
            return ServiceResult(status="needs_info", message=plan.missing_info, calls=[{"engine": engine}])
        if not calls:
            msg = "That needs access you don't have." if dropped else (plan.missing_info or "I couldn't find a college service for that.")
            return ServiceResult(status="needs_info", message=msg, calls=[{"engine": engine, "dropped": dropped}])

        results = await asyncio.gather(*(self._read(c, token) for c in reads))
        failed = [r for r in results if not r["ok"]]
        if failed and all(r.get("retryable") for r in failed) and len(failed) == len(results) and not writes:
            return ServiceResult(status="error", calls=list(results), error={"message": failed[0]["error"], "retryable": True})

        if writes:
            staged = await self._prepare_write(writes[0], state)
            staged.calls = list(results) + staged.calls
            return staged
        return ServiceResult(status="ok", calls=list(results))

    async def _read(self, call: ApiCall, token: str | None) -> dict:
        spec = CATALOG[call.api]
        args = _coerce(call.arg_dict())
        try:
            path, params = _build(spec, args)
            data = await college.request("GET", path, token=token, params=params)
            return {"api": call.api, "args": args, "ok": True, "data": data}
        except CollegeError as exc:
            return {"api": call.api, "args": args, "ok": False, "error": exc.message, "retryable": exc.retryable}
        except KeyError as exc:
            return {"api": call.api, "args": args, "ok": False, "error": f"missing argument {exc}", "retryable": False}

    # ---------------------------------------------------------------- writes
    async def _prepare_write(self, call: ApiCall, state: dict) -> ServiceResult:
        spec, args, token = CATALOG[call.api], _coerce(call.arg_dict()), state.get("token")
        missing = [p for p in spec.required if args.get(p) in (None, "")]
        if missing:
            return ServiceResult(status="needs_info", message=f"I still need: {', '.join(missing).replace('_', ' ')}.")
        try:
            if spec.id == "submit_leave_request":
                return await self._prepare_leave(spec, args, state)
            if spec.id == "submit_admission_application":
                return await self._prepare_admission(spec, args)
            if spec.id == "cancel_request":
                reqs = await college.request("GET", "/v1/me/requests", token=token)
                r = next((x for x in reqs if x["id"] == args["id"]), None)
                if not r:
                    return ServiceResult(status="blocked", message=f"No request #{args['id']} found among your requests.")
                if r["status"] in ("cancelled", "rejected"):
                    return ServiceResult(status="blocked", message=f"Request #{r['id']} is already {r['status']}.")
                return _stage(spec, args, f"Cancel your {r['type'].replace('_', ' ')} request #{r['id']} for {pretty(r['from_date'])}?")
            if spec.id == "register_placement":
                return await self._prepare_placement(spec, args, token)
            if spec.id in ("approve_request", "reject_request"):
                reqs, classes, today = await asyncio.gather(
                    college.request("GET", "/v1/teacher/requests", token=token),
                    college.request("GET", "/v1/teacher/classes", token=token),
                    college.today(),
                )
                r = next((x for x in reqs if x["id"] == args["id"]), None)
                ok, why = can_decide(r, classes, today["date"])
                if not ok:
                    return ServiceResult(status="blocked", message=why)
                what = f"{r['student_name']}'s {r['type'].replace('_', ' ')} request #{r['id']} for {pretty(r['from_date'])}"
                q = f"Approve {what}?" if spec.id == "approve_request" else f'Reject {what} with reason "{args["reason"]}"?'
                return _stage(spec, args, q)
        except CollegeError as exc:
            return ServiceResult(status="error", error={"message": exc.message, "retryable": exc.retryable})
        return ServiceResult(status="blocked", message="That action is not supported.")

    async def _prepare_leave(self, spec: ApiSpec, args: dict, state: dict) -> ServiceResult:
        token, today = state.get("token"), state["today"]
        draft = {
            "type": args.get("type"),
            "from_date": args.get("from_date"),
            "to_date": args.get("to_date") or args.get("from_date"),
            "half_day": bool(args.get("half_day")),
            "reason": str(args.get("reason", "")).strip(),
            "event_name": (str(args.get("event_name") or "").strip() or None) if args.get("type") == "on_duty" else None,
        }
        end = draft["to_date"] if str(draft["to_date"]) > today else today
        calendar, timetable, attendance, requests = await asyncio.gather(
            _safe(college.request("GET", "/v1/calendar", token=token, params={"from": add_days(today, -200), "to": add_days(end, 30)}), []),
            _safe(college.request("GET", "/v1/me/timetable", token=token), []),
            _safe(college.request("GET", "/v1/me/attendance", token=token), None),
            _safe(college.request("GET", "/v1/me/requests", token=token), []),
        )
        reason_missing = draft["reason"].upper() in ("", "TBD", "NONE", "NOT GIVEN")
        check = {**draft, "reason": "pending" if reason_missing else draft["reason"]}
        ev = evaluate_request(check, today=today, calendar=calendar, timetable=timetable, attendance=attendance, requests=requests)
        if not ev.ok:
            return ServiceResult(status="blocked", policy=ev.to_dict(), message=ev.summary())
        if reason_missing:  # rulebook check passed - now we only need the reason before staging
            extra = f" {ev.summary()}" if ev.warnings else ""
            return ServiceResult(status="needs_info", policy=ev.to_dict(), message=f"That date is allowed by the rules.{extra} What is the reason for the leave?")
        label = {"personal_leave": "personal leave", "medical_leave": "medical leave", "on_duty": "on-duty"}[draft["type"]]
        when = pretty(draft["from_date"]) if draft["from_date"] == draft["to_date"] else f"{pretty(draft['from_date'])} to {pretty(draft['to_date'])}"
        warn = (" Note: " + " ".join(w["msg"] for w in ev.warnings)) if ev.warnings else ""
        body = {k: v for k, v in draft.items() if v is not None}
        question = f"Submit {'half-day ' if draft['half_day'] else ''}{label} for {when}{' for ' + draft['event_name'] if draft['event_name'] else ''}, reason: {draft['reason']}?{warn}"
        res = _stage(spec, body, question)
        res.policy = ev.to_dict()
        return res

    async def _prepare_admission(self, spec: ApiSpec, args: dict) -> ServiceResult:
        name = str(args.get("full_name", "")).strip()
        phone = re.sub(r"[^\d+]", "", str(args.get("phone", "")))
        email = str(args.get("email", "")).strip().lower()
        try:
            pct = float(args.get("twelfth_percent"))
        except (TypeError, ValueError):
            pct = -1
        problems = []
        if len(name) < 2:
            problems.append("your full name")
        if len(re.sub(r"\D", "", phone)) < 10:
            problems.append("a 10-digit mobile number")
        if not EMAIL_RE.match(email):
            problems.append("a valid email address")
        if not 0 <= pct <= 100:
            problems.append("your 12th percentage")
        if problems:
            return ServiceResult(status="needs_info", message=f"Please tell me {', '.join(problems)}.")
        courses = await college.courses()
        code = str(args.get("course_code", "")).upper()
        course = next((c for c in courses if c["code"] == code), None)
        if not course:
            return ServiceResult(status="needs_info", message=f"Which course? Options: {', '.join(c['code'] for c in courses)}.")
        if pct < course["min_12th_percent"]:
            alts = [f"{c['name']} (minimum {c['min_12th_percent']}%)" for c in courses if pct >= c["min_12th_percent"]]
            return ServiceResult(
                status="blocked",
                message=f"Not eligible: {course['name']} needs at least {course['min_12th_percent']}% in 12th PCM. Eligible options: {', '.join(alts) or 'none'}.",
                policy={"eligible_alternatives": alts},
            )
        body = {"full_name": name, "phone": phone, "email": email, "course_code": code, "twelfth_percent": pct}
        return _stage(spec, body, f"Submit an application for {course['name']} for {name}, phone {phone}, email {email}, 12th percentage {pct:g}?")

    async def _prepare_placement(self, spec: ApiSpec, args: dict, token: str | None) -> ServiceResult:
        drives, me, ac, today, mine = await asyncio.gather(
            college.request("GET", "/v1/placements/drives", token=token),
            college.request("GET", "/v1/me", token=token),
            college.request("GET", "/v1/me/academics", token=token),
            college.today(),
            _safe(college.request("GET", "/v1/me/placements", token=token), []),
        )
        d = next((x for x in drives if x["id"] == args["id"]), None)
        if not d:
            return ServiceResult(status="blocked", message=f"Drive {args['id']} is not active. Active drives: {', '.join(f'{x['id']} {x['company']}' for x in drives)}.")
        why = []
        if ac.get("cgpa", 0) < d["min_cgpa"]:
            why.append(f"needs CGPA {d['min_cgpa']}, you have {ac.get('cgpa')}")
        if not d.get("allow_standing_arrears") and ac.get("standing_arrears", 0) > 0:
            why.append("no standing arrears allowed")
        if d.get("allowed_departments") and me.get("department") not in d["allowed_departments"]:
            why.append(f"open only to {', '.join(d['allowed_departments'])}")
        if d.get("eligible_years") and me.get("year_of_study") not in d["eligible_years"]:
            why.append(f"open only to year {', '.join(map(str, d['eligible_years']))} students; you are in year {me.get('year_of_study')}")
        if d["deadline"] < today["date"]:
            why.append(f"the deadline {d['deadline']} has passed")
        if d.get("seats_left", 1) <= 0:
            why.append("no seats left")
        if any((m.get("drive_id") or m.get("id")) == d["id"] for m in mine):
            why.append("you are already registered")
        if why:
            return ServiceResult(status="blocked", message=f"You can't register for {d['company']}: " + "; ".join(why) + ".")
        return _stage(spec, {"id": d["id"]}, f"Register you for the {d['company']} drive, deadline {pretty(d['deadline'])}?")

    # --------------------------------------------------------------- confirm
    async def execute_pending(self, pending: dict, token: str | None) -> ServiceResult:
        spec = CATALOG[pending["api"]]
        try:
            path, _ = _build(spec, pending["args"])
            body = {k: pending["args"][k] for k in spec.body if k in pending["args"]} if spec.body else None
            data = await college.request("POST", path, token=token, json=body, idempotency_key=pending["idempotency_key"])
            return ServiceResult(status="executed", calls=[{"api": spec.id, "ok": True, "data": data}], message=_done_message(spec.id, pending["args"], data))
        except CollegeError as exc:
            return ServiceResult(status="error", calls=[{"api": spec.id, "ok": False, "error": exc.message}], error={"message": exc.message, "retryable": exc.retryable})


# ---------------------------------------------------------------- helpers
def _stage(spec: ApiSpec, args: dict, question: str) -> ServiceResult:
    pending = {"api": spec.id, "args": args, "question": question, "idempotency_key": str(uuid.uuid4())}
    return ServiceResult(status="staged", pending=pending, message=question)


def _build(spec: ApiSpec, args: dict) -> tuple[str, dict]:
    names = re.findall(r"{(\w+)}", spec.path)
    path = spec.path.format(**{n: args[n] for n in names})
    params = {k: v for k, v in args.items() if k not in names and k in spec.params and k not in spec.body}
    return path, params


def _coerce(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        if v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none")):
            continue
        if k in INT_ARGS:
            try:
                v = int(str(v).lstrip("#"))
            except ValueError:
                pass
        elif k == "half_day":
            v = str(v).lower() in ("true", "1", "yes")
        elif k == "twelfth_percent":
            try:
                v = float(str(v).rstrip("%"))
            except ValueError:
                pass
        out[k] = v
    return out


async def _safe(coro, default):
    try:
        return await coro
    except CollegeError:
        return default


def _done_message(api: str, args: dict, data: Any) -> str:
    data = data or {}
    if api == "submit_leave_request":
        msg = f"Submitted. Your {args['type'].replace('_', ' ')} request number {data.get('id')} is {data.get('status', 'pending')}. Your class advisor will review it."
        if args["type"] == "on_duty":
            msg += " Remember to submit proof of participation within 3 working days after the event."
        if args["type"] == "medical_leave":
            msg += " Submit your medical certificate within 3 working days of returning."
        return msg
    if api == "submit_admission_application":
        no = data.get("application_no") or data.get("application_number") or data.get("id")
        return f"Done. Your application{f' number {no}' if no else ''} is submitted. You will get an email confirmation. There is no application fee."
    if api == "cancel_request":
        return f"Request {args['id']} is cancelled."
    if api == "register_placement":
        return "You are registered for the drive. All the best!"
    if api == "approve_request":
        return f"Request {args['id']} is approved."
    if api == "reject_request":
        return f"Request {args['id']} is rejected."
    return "Done."


# ------------------------------------------------- rule planner (no LLM)
def plan_by_rules(state: dict) -> ServicePlan:
    q, role, today = state["user_query"].lower(), state.get("role", "visitor"), state["today"]
    call = lambda api, **kw: ApiCall(api=api, args=[ApiArg(name=k, value=str(v)) for k, v in kw.items()])  # noqa: E731
    dates = extract_dates(q, today)
    idm = re.search(r"(?:request|number|#|drive)\s*(\d+)", q)
    if role == "teacher":
        if idm and re.search(r"\bapprove\b", q):
            return ServicePlan(calls=[call("approve_request", id=idm.group(1))])
        if idm and re.search(r"\breject\b", q):
            reason = (re.split(r"because|reason|as ", state["user_query"], maxsplit=1, flags=re.I) + [""])[1].strip()
            return ServicePlan(calls=[call("reject_request", id=idm.group(1), reason=reason)] if reason else [], missing_info="" if reason else "What is the reason for rejecting it?")
        if re.search(r"attendance", q):
            return ServicePlan(calls=[call("teacher_classes")])
        return ServicePlan(calls=[call("teacher_requests", status="pending")])
    if role == "student":
        if re.search(r"\b(leave|on.?duty|od|sick)\b", q) and re.search(r"apply|take|want|need|submit|can i", q):
            if not dates:
                return ServicePlan(missing_info="Which date do you need it for?")
            t = "medical_leave" if re.search(r"sick|fever|ill|medical|hospital", q) else "on_duty" if re.search(r"on.?duty|\bod\b|symposium|hackathon|sports|event", q) else "personal_leave"
            kw = {"type": t, "from_date": dates[0], "to_date": dates[-1], "reason": state["user_query"]}
            if t == "on_duty":
                kw["event_name"] = state["user_query"]
            return ServicePlan(calls=[call("submit_leave_request", **kw)])
        if idm and "cancel" in q:
            return ServicePlan(calls=[call("cancel_request", id=idm.group(1))])
        if re.search(r"attendance|percent|bunk", q):
            return ServicePlan(calls=[call("my_attendance")])
        if re.search(r"timetable|schedule|class|period", q):
            d = dates[0] if dates else today
            return ServicePlan(calls=[call("my_timetable"), call("calendar", **{"from": d, "to": d})])
        if re.search(r"request|status", q):
            return ServicePlan(calls=[call("my_requests")])
        if re.search(r"holiday|exam|calendar|internal", q):
            return ServicePlan(calls=[call("calendar", **{"from": today, "to": add_days(today, 45)})])
        if re.search(r"placement|drive|compan", q):
            return ServicePlan(calls=[call("placement_drives"), call("my_academics")])
        if re.search(r"cgpa|arrear|profile", q):
            return ServicePlan(calls=[call("my_academics")])
    if re.search(r"placement|drive|compan|recruit", q):
        return ServicePlan(calls=[call("placement_drives")])
    if re.search(r"apply|application", q) and role == "visitor":
        return ServicePlan(missing_info="I can file your application. Please tell me your full name, mobile number, email, the course and your 12th percentage.")
    return ServicePlan(calls=[call("list_courses")])
