"""Policy Guard: simulate a leave / on-duty request against the rulebook BEFORE
it is submitted, so the student hears the consequences up front.

Pure functions (all data passed in) -> easy to test.

Rules (KIT Student Handbook 2026 + Circular 12/2026, B.Tech):
  4.1.1 75% minimum per course       4.2.3 planned-leave impact formula
  6.1.1 24h notice                   6.1.2 no retrospective personal leave
  6.1.3 >3 working days needs HoD    6.1.4 no personal leave on IA day
  6.1.6 dates in order + reason      6.3.x medical leave + certificate
  6.4.2 OD before event + event name 6.4.5 max 10 OD days / semester
  6.4.7 no OD on IA day              Circ 12/2026 OD proof in 3 working days
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

REQUEST_TYPES = ("personal_leave", "medical_leave", "on_duty")
WEEKDAYS = ["", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EXAM_RE = re.compile(r"exam|assessment|test", re.I)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse(d: str) -> date:
    return date.fromisoformat(d)


def add_days(d: str, n: int) -> str:
    return (parse(d) + timedelta(days=n)).isoformat()


def weekday_of(d: str) -> int:
    """College API convention: 1 = Monday ... 7 = Sunday."""
    return parse(d).isoweekday()


def pretty(d: str) -> str:
    x = parse(d)
    return f"{WEEKDAYS[x.isoweekday()]}, {x.day} {x.strftime('%B')}"


def date_range(start: str, end: str, limit: int = 62) -> list[str]:
    out, cur = [], parse(start)
    while cur <= parse(end) and len(out) < limit:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def effective_weekday(d: str, calendar: list[dict]) -> int | None:
    """Which weekday's timetable a date follows (None = not a working day)."""
    events = [e for e in calendar if e.get("date") == d and e.get("status") != "cancelled"]
    for e in events:
        if e.get("follows_weekday"):
            return int(e["follows_weekday"])  # declared working Saturday (Rule 3.4.3)
    if any("holiday" in str(e.get("type", "")).lower() for e in events):
        return None
    wd = weekday_of(d)
    return None if wd in (6, 7) else wd  # Sundays always, Saturdays normally (3.3.3, 3.4.1)


def working_days(start: str, end: str, calendar: list[dict]) -> list[str]:
    return [d for d in date_range(start, end) if effective_weekday(d, calendar) is not None]


def attendance_impact(days: list[str], calendar: list[dict], timetable: list[dict], attendance: dict | None, half_day: bool = False) -> list[dict]:
    """Rule 4.2.3: add scheduled periods on leave days to 'held'; attended stays the same."""
    missed: dict[str, int] = {}
    for d in days:
        wd = effective_weekday(d, calendar)
        slots = [t for t in timetable if t.get("weekday") == wd]
        if half_day:  # unknown half -> assume the worse of morning (P1-3) / afternoon (P4-6)
            am = [t for t in slots if t["period"] <= 3]
            pm = [t for t in slots if t["period"] >= 4]
            slots = am if len(am) >= len(pm) else pm
        for s in slots:
            missed[s["subject_code"]] = missed.get(s["subject_code"], 0) + 1
    rows = []
    for s in (attendance or {}).get("subjects", []):
        m = missed.get(s["subject_code"], 0)
        if not m or not s.get("classes_held"):
            continue
        credited = s["classes_attended"] + (s.get("classes_od") or 0)
        before = round(credited / s["classes_held"] * 100, 1)
        after = round(credited / (s["classes_held"] + m) * 100, 1)
        rows.append({"subject": s["subject_code"], "name": s["subject_name"], "missed": m, "before": before, "after": after, "below_min": after < 75})
    return rows


@dataclass
class Evaluation:
    ok: bool
    blockers: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    info: list[dict] = field(default_factory=list)
    impact: list[dict] = field(default_factory=list)
    days: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.blockers:
            parts.append("Not allowed: " + " ".join(f"{b['msg']} (Rule {b['rule']})" for b in self.blockers))
        if self.warnings:
            parts.append("Warning: " + " ".join(f"{w['msg']} (Rule {w['rule']})" for w in self.warnings))
        if self.info:
            parts.append(" ".join(i["msg"] for i in self.info))
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {"allowed": self.ok, "blockers": self.blockers, "warnings": self.warnings, "info": self.info, "attendance_impact": self.impact, "working_days": self.days}


def _semester_start(today: str) -> str:
    y, m = int(today[:4]), int(today[5:7])
    return f"{y}-07-01" if m >= 7 else f"{y}-01-01"


def evaluate_request(draft: dict, *, today: str, calendar: list, timetable: list, attendance: dict | None, requests: list) -> Evaluation:
    ev = Evaluation(ok=False)
    rtype = draft.get("type")
    if rtype not in REQUEST_TYPES:
        ev.blockers.append({"rule": "API", "msg": "Request type must be personal leave, medical leave or on-duty."})
        return ev
    f, t = str(draft.get("from_date") or ""), str(draft.get("to_date") or draft.get("from_date") or "")
    if not ISO_RE.match(f) or not ISO_RE.match(t):
        ev.blockers.append({"rule": "6.1.6", "msg": "I need clear start and end dates."})
        return ev
    if f > t:
        ev.blockers.append({"rule": "6.1.6", "msg": "The end date is before the start date."})
    if len(str(draft.get("reason") or "").strip()) < 3:
        ev.blockers.append({"rule": "6.1.6", "msg": "A reason is required."})
    if rtype == "on_duty" and not str(draft.get("event_name") or "").strip():
        ev.blockers.append({"rule": "6.4.2", "msg": "On-duty requests must name the event."})
    if draft.get("half_day") and f != t:
        ev.blockers.append({"rule": "6.2.1", "msg": "Half-day leave covers a single working day."})
    if ev.blockers:
        return ev

    ev.days = working_days(f, t, calendar)
    if not ev.days:
        ev.blockers.append({"rule": "3.3", "msg": "Every day in that range is already a holiday, so no request is needed."})
        return ev

    exams = [e for e in calendar if EXAM_RE.search(str(e.get("type", ""))) and e.get("status") != "cancelled" and f <= e["date"] <= t]
    if exams:
        listing = ", ".join(f"{e['title']} on {pretty(e['date'])}" for e in exams)
        if rtype == "personal_leave":
            ev.blockers.append({"rule": "6.1.4", "msg": f"Personal leave is not granted on an Internal Assessment day: {listing}."})
        elif rtype == "on_duty":
            ev.blockers.append({"rule": "6.4.7", "msg": f"On-duty is not granted on an Internal Assessment day ({listing}) unless it is a university or national-level event approved in writing by the Principal."})
        else:
            ev.warnings.append({"rule": "8.3", "msg": f"Note: {listing} falls in this period."})

    if rtype == "personal_leave":
        if f <= today:
            ev.blockers.append({"rule": "6.1.2", "msg": "Personal leave cannot be applied for the same day or after the absence. If you were ill, apply for medical leave instead."})
        elif f == add_days(today, 1):
            ev.warnings.append({"rule": "6.1.1", "msg": "Personal leave needs 24 hours notice; a request this close may be rejected."})
        if len(ev.days) > 3:
            ev.warnings.append({"rule": "6.1.3", "msg": f"This is {len(ev.days)} working days, so it also needs Head of Department approval."})
    if rtype == "medical_leave":
        ev.info.append({"rule": "6.3.2", "msg": "Submit the medical certificate to your class advisor within 3 working days of returning, or the leave lapses."})
        if len(ev.days) >= 3:
            ev.info.append({"rule": "6.3.2", "msg": "For 3 or more days a fitness certificate is also required."})
    if rtype == "on_duty":
        if f <= today:
            ev.blockers.append({"rule": "6.4.2", "msg": "An on-duty request must be submitted before the event."})
        sem = _semester_start(today)
        used = sum(
            len(working_days(r["from_date"], r["to_date"], calendar))
            for r in requests
            if r.get("type") == "on_duty" and r.get("status") in ("approved", "pending") and r.get("from_date", "") >= sem
        )
        if used + len(ev.days) > 10:
            ev.blockers.append({"rule": "6.4.5", "msg": f"On-duty is capped at 10 working days a semester; you already have {used} approved or pending."})
        ev.info.append({"rule": "Circular 12/2026", "msg": "Submit proof of participation within 3 working days after the event (changed from 5 days from 1 November 2026)."})

    for r in requests:
        if r.get("status") in ("pending", "approved", "proof_pending") and r["from_date"] <= t and r["to_date"] >= f:
            span = r["from_date"] if r["from_date"] == r["to_date"] else f"{r['from_date']} to {r['to_date']}"
            ev.blockers.append({"rule": "6.5.5", "msg": f"You already have a {r['status']} {r['type'].replace('_', ' ')} request (#{r['id']}) covering {span}."})
            break

    if rtype != "on_duty" and attendance:  # OD counts as attended
        ev.impact = attendance_impact(ev.days, calendar, timetable, attendance, bool(draft.get("half_day")))
        for row in ev.impact:
            if row["below_min"]:
                ev.warnings.append({"rule": "4.1.1", "msg": f"{row['name']} would drop from {row['before']}% to {row['after']}%, below the 75% minimum."})

    ev.ok = not ev.blockers
    return ev


def can_decide(request: dict | None, classes: list[dict], today: str) -> tuple[bool, str]:
    """Teacher-side guard (Rules 6.5.2, 6.5.3, 6.5.5)."""
    if not request:
        return False, "I could not find that request among your classes' requests."
    if request.get("status") not in ("pending", "proof_pending"):
        return False, f"Request #{request['id']} is already {request['status']}; a request is decided only once (Rule 6.5.5)."
    cls = next((c for c in classes if c.get("class_code") == request.get("class_code")), None)
    if not cls:
        return False, f"You are not the advisor for {request.get('class_code')}, so you cannot decide this request (Rule 6.5.2)."
    if cls.get("valid_until") and cls["valid_until"] < today:
        return False, f"Your delegation for {cls['class_code']} ended on {cls['valid_until']} (Rule 6.5.3)."
    return True, ""
