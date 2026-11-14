"""Date helpers: every relative date is resolved against the COLLEGE date, not the server clock."""

import re

from src.services.policy import WEEKDAYS, add_days, parse

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def extract_dates(text: str, today: str) -> list[str]:
    """Pull dates out of free speech (used by the no-LLM fallback)."""
    t = text.lower()
    found: list[str] = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", t)
    if re.search(r"\bday after tomorrow\b", t):
        found.append(add_days(today, 2))
    elif re.search(r"\btomorrow\b", t):
        found.append(add_days(today, 1))
    if re.search(r"\btoday\b", t):
        found.append(today)
    wd_today = parse(today).isoweekday()
    for i, d in enumerate(DAYS):
        if re.search(rf"\b{d}\b", t):
            delta = (i + 1 - wd_today) % 7 or 7
            found.append(add_days(today, delta))
    pairs = [(m.group(1), m.group(2)) for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}", t)]
    pairs += [(m.group(2), m.group(1)) for m in re.finditer(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)]
    year = int(today[:4])
    for day, mon in pairs:
        cand = f"{year}-{MONTHS.index(mon[:3]) + 1:02d}-{int(day):02d}"
        try:
            parse(cand)
        except ValueError:
            continue
        found.append(f"{year + 1}{cand[4:]}" if cand < add_days(today, -60) else cand)
    return sorted(set(found))


def date_table(today: str, back: int = 1, ahead: int = 13) -> str:
    rows = []
    for i in range(-back, ahead + 1):
        d = add_days(today, i)
        tag = " (today)" if i == 0 else " (tomorrow)" if i == 1 else " (yesterday)" if i == -1 else ""
        rows.append(f"{WEEKDAYS[parse(d).isoweekday()][:3]} {d}{tag}")
    return ", ".join(rows)
