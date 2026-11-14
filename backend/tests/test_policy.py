"""Policy Guard - fixture mirrors the real sandbox: college today is Sat 2026-11-21,
DBMS internal on Mon 2026-11-23, Arjun at 38/50 in CS304."""

from src.services.policy import attendance_impact, can_decide, effective_weekday, evaluate_request, weekday_of, working_days

TODAY = "2026-11-21"
CAL = [
    {"date": "2026-11-22", "type": "holiday", "title": "Sunday Holiday", "status": "active"},
    {"date": "2026-11-23", "type": "internal_exam", "title": "DBMS Internal Assessment Exam", "subject_code": "CS304", "status": "active"},
]
TT = [
    {"weekday": 1, "period": 1, "subject_code": "CS301"},
    {"weekday": 1, "period": 2, "subject_code": "CS304"},
    {"weekday": 4, "period": 1, "subject_code": "CS304"},
    {"weekday": 4, "period": 3, "subject_code": "CS304"},
    {"weekday": 4, "period": 4, "subject_code": "CS303"},
]
ATT = {"subjects": [{"subject_code": "CS304", "subject_name": "Database Management Systems", "classes_held": 50, "classes_attended": 38, "classes_od": 0}]}


def ev(draft, requests=None):
    return evaluate_request(draft, today=TODAY, calendar=CAL, timetable=TT, attendance=ATT, requests=requests or [])


def leave(**kw):
    return {"type": "personal_leave", "reason": "Family function", **kw}


def test_weekday_convention():
    assert weekday_of("2026-11-23") == 1 and weekday_of("2026-11-22") == 7


def test_blocks_personal_leave_on_internal_assessment():
    r = ev(leave(from_date="2026-11-23", to_date="2026-11-23"))
    assert not r.ok and any(b["rule"] == "6.1.4" for b in r.blockers)


def test_blocks_on_duty_on_internal_assessment():
    r = ev({"type": "on_duty", "from_date": "2026-11-23", "to_date": "2026-11-23", "reason": "Symposium", "event_name": "X"})
    assert any(b["rule"] == "6.4.7" for b in r.blockers)


def test_attendance_impact_rule_4_2_3():
    r = ev(leave(from_date="2026-11-26", to_date="2026-11-26"))
    row = next(x for x in r.impact if x["subject"] == "CS304")
    assert r.ok and row["missed"] == 2 and row["before"] == 76.0 and row["after"] == 73.1  # 38/52
    assert any(w["rule"] == "4.1.1" for w in r.warnings)


def test_no_retrospective_personal_leave():
    assert any(b["rule"] == "6.1.2" for b in ev(leave(from_date="2026-11-20", to_date="2026-11-20")).blockers)


def test_medical_leave_after_absence_allowed_with_certificate_reminder():
    r = ev({"type": "medical_leave", "from_date": "2026-11-19", "to_date": "2026-11-20", "reason": "Fever"})
    assert r.ok and any("certificate" in i["msg"] for i in r.info)


def test_on_duty_needs_event_and_mentions_circular():
    assert any(b["rule"] == "6.4.2" for b in ev({"type": "on_duty", "from_date": "2026-11-30", "to_date": "2026-11-30", "reason": "Event"}).blockers)
    r = ev({"type": "on_duty", "from_date": "2026-11-30", "to_date": "2026-11-30", "reason": "Paper", "event_name": "Symposium"})
    assert r.ok and any("3 working days" in i["msg"] for i in r.info)


def test_on_duty_cap_10_days():
    reqs = [{"id": 1, "type": "on_duty", "status": "approved", "from_date": "2026-11-02", "to_date": "2026-11-13"}]
    r = ev({"type": "on_duty", "from_date": "2026-11-30", "to_date": "2026-11-30", "reason": "Hackathon", "event_name": "SIH"}, reqs)
    assert any(b["rule"] == "6.4.5" for b in r.blockers)


def test_overlapping_request_blocked():
    reqs = [{"id": 67, "type": "on_duty", "status": "pending", "from_date": "2026-11-30", "to_date": "2026-11-30"}]
    assert any("#67" in b["msg"] for b in ev(leave(from_date="2026-11-30", to_date="2026-11-30"), reqs).blockers)


def test_dates_and_reason_validated():
    r = ev({"type": "personal_leave", "from_date": "2026-11-27", "to_date": "2026-11-26", "reason": ""})
    assert not r.ok and len(r.blockers) >= 2


def test_holiday_only_range():
    assert any("holiday" in b["msg"] for b in ev(leave(from_date="2026-11-22", to_date="2026-11-22")).blockers)


def test_working_saturday_follows_named_weekday():
    cal = [{"date": "2026-11-28", "type": "working_saturday", "follows_weekday": 4, "status": "active"}]
    assert effective_weekday("2026-11-28", cal) == 4
    assert working_days("2026-11-27", "2026-11-29", cal) == ["2026-11-27", "2026-11-28"]
    assert attendance_impact(["2026-11-28"], cal, TT, ATT)[0]["missed"] == 2


def test_teacher_decision_guard():
    classes = [{"class_code": "CSE-2A", "valid_until": None}]
    assert can_decide({"id": 1, "status": "pending", "class_code": "CSE-2A"}, classes, TODAY)[0]
    assert not can_decide({"id": 1, "status": "approved", "class_code": "CSE-2A"}, classes, TODAY)[0]
    assert not can_decide({"id": 1, "status": "pending", "class_code": "ECE-2B"}, classes, TODAY)[0]
    assert not can_decide({"id": 1, "status": "pending", "class_code": "CSE-2A"}, [{"class_code": "CSE-2A", "valid_until": "2026-11-01"}], TODAY)[0]
