"""The Service Agent's knowledge base: every college API it may call.

Each entry says who may call it (role scoping is enforced here, server-side,
whatever the LLM asks for), what it does, its parameters, and whether it is a
read or a write. Writes are never executed directly: they are validated, run
through the Policy Guard and staged for human confirmation.
"""

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["visitor", "student", "teacher"]


@dataclass(frozen=True)
class ApiSpec:
    id: str
    method: Literal["GET", "POST"]
    path: str  # may contain {id} / {class_id}
    roles: tuple[Role, ...]
    description: str
    params: dict[str, str] = field(default_factory=dict)  # name -> description
    required: tuple[str, ...] = ()
    kind: Literal["read", "write"] = "read"
    body: tuple[str, ...] = ()  # params that go in the JSON body (writes)


ALL: tuple[Role, ...] = ("visitor", "student", "teacher")
STUDENT: tuple[Role, ...] = ("student",)
TEACHER: tuple[Role, ...] = ("teacher",)
LOGGED_IN: tuple[Role, ...] = ("student", "teacher")

CATALOG: dict[str, ApiSpec] = {
    s.id: s
    for s in [
        # ---------------- public / visitor ----------------
        ApiSpec("college_profile", "GET", "/v1/college", ALL, "College name, intro and contact details."),
        ApiSpec("today", "GET", "/v1/today", ALL, "The college's current date."),
        ApiSpec("list_courses", "GET", "/v1/courses", ALL, "B.Tech courses: code, minimum 12th %, annual fee, hostel fee, seats."),
        ApiSpec("placement_drives", "GET", "/v1/placements/drives", ("visitor", "student"), "Active placement drives with eligibility (CGPA, departments, years), deadline, seats left."),
        ApiSpec(
            "submit_admission_application",
            "POST",
            "/v1/admissions/applications",
            ("visitor",),
            "Submit an admission application for a prospective student.",
            {
                "full_name": "applicant full name",
                "phone": "mobile number",
                "email": "email address",
                "course_code": "BTECH-CSE | BTECH-ECE | BTECH-MECH",
                "twelfth_percent": "12th PCM aggregate % (number)",
            },
            ("full_name", "phone", "email", "course_code", "twelfth_percent"),
            "write",
            ("full_name", "phone", "email", "course_code", "twelfth_percent"),
        ),
        # ---------------- student ----------------
        ApiSpec("my_profile", "GET", "/v1/me", LOGGED_IN, "Logged-in user's profile (name, class, department, year)."),
        ApiSpec("my_academics", "GET", "/v1/me/academics", STUDENT, "Student CGPA, standing arrears, placement status."),
        ApiSpec("my_subjects", "GET", "/v1/me/subjects", STUDENT, "Subjects the student is enrolled in."),
        ApiSpec("my_timetable", "GET", "/v1/me/timetable", STUDENT, "Weekly timetable (weekday 1=Mon..7=Sun, period, subject)."),
        ApiSpec("calendar", "GET", "/v1/calendar", LOGGED_IN, "Holidays, internal exams and working Saturdays in a date range.", {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}),
        ApiSpec("my_attendance", "GET", "/v1/me/attendance", STUDENT, "Attendance summary per subject (held, attended, OD, percentage)."),
        ApiSpec(
            "my_attendance_sessions",
            "GET",
            "/v1/me/attendance/sessions",
            STUDENT,
            "Period-by-period attendance (dates absent) for a subject.",
            {"subject": "subject code e.g. CS304", "from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
        ),
        ApiSpec("my_requests", "GET", "/v1/me/requests", STUDENT, "Student's leave / on-duty requests and their status."),
        ApiSpec(
            "submit_leave_request",
            "POST",
            "/v1/me/requests",
            STUDENT,
            "Submit a personal leave, medical leave or on-duty request.",
            {
                "type": "personal_leave | medical_leave | on_duty",
                "from_date": "YYYY-MM-DD",
                "to_date": "YYYY-MM-DD",
                "half_day": "true/false",
                "reason": "reason in the student's words",
                "event_name": "event name (required for on_duty)",
            },
            ("type", "from_date", "to_date", "reason"),
            "write",
            ("type", "from_date", "to_date", "half_day", "reason", "event_name"),
        ),
        ApiSpec("cancel_request", "POST", "/v1/me/requests/{id}/cancel", STUDENT, "Cancel one of the student's requests.", {"id": "request id"}, ("id",), "write"),
        ApiSpec("my_placements", "GET", "/v1/me/placements", STUDENT, "Student's placement drive registrations."),
        ApiSpec("register_placement", "POST", "/v1/placements/drives/{id}/register", STUDENT, "Register for a placement drive.", {"id": "drive id"}, ("id",), "write"),
        # ---------------- teacher ----------------
        ApiSpec("teacher_classes", "GET", "/v1/teacher/classes", TEACHER, "Classes the teacher advises (can approve requests for)."),
        ApiSpec("teacher_requests", "GET", "/v1/teacher/requests", TEACHER, "Student requests for the teacher's classes.", {"status": "pending | approved | rejected | cancelled (omit for all)"}),
        ApiSpec("class_attendance", "GET", "/v1/teacher/classes/{class_id}/attendance", TEACHER, "Attendance sheet for a class.", {"class_id": "class id"}, ("class_id",)),
        ApiSpec("approve_request", "POST", "/v1/teacher/requests/{id}/approve", TEACHER, "Approve a student request.", {"id": "request id"}, ("id",), "write"),
        ApiSpec(
            "reject_request",
            "POST",
            "/v1/teacher/requests/{id}/reject",
            TEACHER,
            "Reject a student request (reason is mandatory - never invent it).",
            {"id": "request id", "reason": "teacher's reason"},
            ("id", "reason"),
            "write",
            ("reason",),
        ),
    ]
}


def apis_for(role: Role) -> list[ApiSpec]:
    return [s for s in CATALOG.values() if role in s.roles]


def allowed(role: Role, api_id: str) -> bool:
    spec = CATALOG.get(api_id)
    return bool(spec and role in spec.roles)


def catalog_prompt(role: Role) -> str:
    """Compact one-line-per-API description for the planner prompt."""
    lines = []
    for s in apis_for(role):
        params = ", ".join(f"{k}{'*' if k in s.required else ''}: {v}" for k, v in s.params.items())
        lines.append(f"- {s.id} [{s.kind}] {s.description}" + (f" Params: {params}" if params else ""))
    return "\n".join(lines)
