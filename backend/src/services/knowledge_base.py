"""Document knowledge base with a trust engine.

* Live-syncs every document from the college system (new circulars appear
  automatically) and chunks them by rule number.
* BM25 keyword search - no embedding API, no extra latency, no extra quota.
* Trust labels (Handbook Rules 1.4.3 / 1.4.4):
    - a circular in force overrides the handbook;
    - only the newest edition already in effect is current, older ones are SUPERSEDED;
    - drafts / proposals / "not adopted" sections are NON-BINDING;
    - sentences aimed at AI assistants are stripped (prompt-injection defence).
"""

import asyncio
import io
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pypdf import PdfReader

from src.services.college_client import college

log = logging.getLogger("kb")

STOP = set(
    "a an the of to in on for and or is are be by with at as it this that from can i my me we you your do does what how when "
    "which who will would should may there their was were has have not no if any all into than then so about many much per "
    "tell please know want get got also just like".split()
)
SYNONYMS = {
    "od": ["duty"],
    "bunk": ["unauthorised", "absence"],
    "bunking": ["unauthorised", "absence"],
    "sick": ["medical"],
    "fees": ["fee"],
    "exam": ["examination", "assessment"],
    "ia": ["internal", "assessment"],
    "internal": ["assessment"],
    "marks": ["percent", "percentage"],
    "eligibility": ["minimum", "eligible"],
    "minimum": ["least"],
    "required": ["must"],
    "shortage": ["condonation", "attendance"],
}
INJECTION_RE = re.compile(
    r"(note|message|instruction)s?\s+(to|for)\s+(ai|assistants?|chatbots?|voice assistants?|llms?)"
    r"|ignore (all |any )?(previous|prior|above)|do not (mention|tell|reveal)|you must (say|tell)|system prompt",
    re.I,
)
RULE_RE = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{1,2})\s")
SECTION_RE = re.compile(r"^(Chapter\s+\d+|Annexure\s+[A-Z]|Table\s+\d|\d{1,2}\.\d{1,2}\s+[A-Z]|\d{1,2}\.\s+[A-Z])")
NONBINDING_RE = re.compile(r"\b(draft|not adopted|proposal)\b", re.I)
HEADER_RE = re.compile(r"page \d+ of \d+", re.I)


def stem(w: str) -> str:
    s = re.sub(r"(ings?|edly|ed|es|s)$", "", w)
    s = re.sub(r"(.)\1$", r"\1", s)
    return s or w


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"[^a-z0-9.%]+", text.lower()):
        w = raw.strip(".")
        if not w or w in STOP:
            continue
        out.append(w if w[0].isdigit() else stem(w))
        out.extend(stem(s) for s in SYNONYMS.get(w, []))
    return out


def sanitize(text: str) -> str:
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if not INJECTION_RE.search(s))


def normalize_lines(page_text: str) -> list[str]:
    """Clean PDF lines and re-join headings that the extractor split word-by-word."""
    page_text = page_text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("�", "·")
    lines = [re.sub(r"\s+", " ", l).strip() for l in page_text.splitlines()]
    lines = [l for l in lines if l and not HEADER_RE.search(l)]
    out: list[str] = []
    buf: list[str] = []
    for line in lines:
        if len(line.split()) <= 2 and not RULE_RE.match(line + " "):
            buf.append(line)
            continue
        if buf:
            out.append(" ".join(buf))
            buf = []
        out.append(line)
    if buf:
        out.append(" ".join(buf))
    return out


@dataclass
class Chunk:
    id: str
    doc_id: int
    title: str
    type: str
    version: str
    page: int
    section: str
    rule: str | None
    non_binding: bool
    text: str
    status: str = "current"
    tokens: list[str] = field(default_factory=list)


def chunk_document(doc: dict, pages: list[str]) -> list[Chunk]:
    chapters: dict[str, str] = {}
    for p in pages:
        for m in re.finditer(r"Chapter\s+(\d+)\s*·\s*([^\n]+)", p):
            chapters.setdefault(m.group(1), f"Chapter {m.group(1)} · {m.group(2).strip()}")
    raw: list[dict] = []
    section, non_binding, heading = "", False, ""
    cur: dict | None = None

    def flush() -> None:
        nonlocal cur
        if cur and len(cur["text"].strip()) > 20:
            raw.append(cur)
        cur = None

    for idx, page in enumerate(pages):
        for line in normalize_lines(page):
            is_heading = bool(SECTION_RE.match(line)) and not RULE_RE.match(line) and len(line) < 90 and not line.endswith((".", ":"))
            if is_heading:
                flush()
                heading = line
                if line.startswith(("Chapter", "Annexure")):
                    section, non_binding = line, bool(NONBINDING_RE.search(line))
                elif not line.startswith("Table"):
                    section = section.split(" > ")[0] + " > " + line
                    if re.match(r"^\d{1,2}\.\s", line):
                        non_binding = bool(NONBINDING_RE.search(line))
                continue
            m = RULE_RE.match(line)
            if m or cur is None or len(cur["text"]) > 900:
                flush()
                cur = {"page": idx + 1, "section": section, "rule": m.group(1) if m else None,
                       "text": f"{heading}: " if heading and not m else "", "nb": non_binding}
                heading = ""
            cur["text"] += line + " "
    flush()
    return [
        Chunk(
            id=f"{doc['id']}:{i}",
            doc_id=doc["id"],
            title=doc["title"],
            type=doc["type"],
            version=str(doc.get("version", "")),
            page=c["page"],
            section=(c["rule"] and chapters.get(c["rule"].split(".")[0])) or c["section"],
            rule=c["rule"],
            non_binding=c["nb"],
            text=sanitize(c["text"].strip()),
        )
        for i, c in enumerate(raw)
    ]


def classify_documents(docs: list[dict], today: str) -> dict[int, str]:
    status: dict[int, str] = {}
    by_type: dict[str, list[dict]] = {}
    for d in docs:
        by_type.setdefault(d["type"], []).append(d)
    for dtype, items in by_type.items():
        if dtype == "circular":
            for d in items:
                status[d["id"]] = "circular-in-force" if d.get("effective_date", "") <= today else "circular-upcoming"
            continue
        in_force = sorted((d for d in items if not d.get("effective_date") or d["effective_date"] <= today),
                          key=lambda d: d.get("effective_date") or "", reverse=True)
        seen: set[str] = set()
        for d in in_force:
            key = d["title"].split(" - ")[0] if dtype == "lesson_notes" else dtype
            status[d["id"]] = "superseded" if key in seen else "current"
            seen.add(key)
        for d in items:
            status.setdefault(d["id"], "upcoming")
    return status


def applies_to_user(applies_to: dict | None, user: dict) -> bool:
    """Does a document's `applies_to` cover this user? Unknown keys are ignored."""
    if not applies_to:
        return True
    if user.get("role") == "teacher":  # teachers need every document that applies to their department
        depts = applies_to.get("departments")
        return not depts or user.get("department") in depts
    checks = {
        "programmes": user.get("programme"),
        "departments": user.get("department"),
        "years": user.get("year_of_study"),
    }
    for key, value in checks.items():
        allowed = applies_to.get(key)
        if allowed and value not in allowed:
            return False
    sem = applies_to.get("semester")
    year = user.get("year_of_study")
    if sem and year and sem not in (2 * year - 1, 2 * year):
        return False
    return True


class KnowledgeBase:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.chunks: list[Chunk] = []
        self.status: dict[int, str] = {}
        self.df: dict[str, int] = {}
        self.avg_len = 1.0
        self._text_cache: dict[str, list[str]] = {}
        self.last_sync: str | None = None
        self.last_error: str | None = None
        self.new_ids: set[int] = set()

    def load(self, docs: list[dict], pages_by_id: dict[int, list[str]], today: str) -> None:
        self.docs = docs
        self.status = classify_documents(docs, today)
        chunks: list[Chunk] = []
        for d in docs:
            for c in chunk_document(d, pages_by_id.get(d["id"], [])):
                c.status = self.status.get(d["id"], "current")
                c.tokens = tokenize(f"{c.section} {c.text}")
                chunks.append(c)
        self.chunks = chunks
        self.df = {}
        total = 0
        for c in chunks:
            total += len(c.tokens)
            for t in set(c.tokens):
                self.df[t] = self.df.get(t, 0) + 1
        self.avg_len = total / max(len(chunks), 1)

    @staticmethod
    def _extract(pdf: bytes) -> list[str]:
        reader = PdfReader(io.BytesIO(pdf))
        return [p.extract_text(extraction_mode="layout") or "" for p in reader.pages]

    async def sync(self) -> None:
        try:
            docs, today = await asyncio.gather(college.documents(), college.today())
            pages: dict[int, list[str]] = {}
            for d in docs:
                key = f"{d['id']}@{d.get('version')}"
                if key not in self._text_cache:
                    pdf = await college.document_file(d["id"])
                    self._text_cache[key] = await asyncio.to_thread(self._extract, pdf)
                pages[d["id"]] = self._text_cache[key]
            before = {f"{d['id']}@{d.get('version')}" for d in self.docs}
            self.load(docs, pages, today["date"])
            added = [d for d in docs if f"{d['id']}@{d.get('version')}" not in before]
            if before and added:  # anything appearing after the first sync is announced as new
                self.new_ids |= {d["id"] for d in added}
                log.info("new documents: %s", ", ".join(d["title"] for d in added))
            self.last_sync = datetime.now(timezone.utc).isoformat()
            self.last_error = None
            log.info("synced %d docs, %d chunks", len(docs), len(self.chunks))
        except Exception as exc:  # keep serving the previous index
            self.last_error = str(exc)
            log.error("kb sync failed: %s", exc)

    def search(self, query: str, k: int = 4, types: list[str] | None = None, programme: str = "UG") -> list[Chunk]:
        q = tokenize(query)
        if not q or not self.chunks:
            return []
        wrong_level = re.compile(r"undergraduate|\(B\.Tech\)" if programme == "PG" else r"postgraduate|\(M\.Tech\)", re.I)
        asked_other = re.search(r"b\.?tech|undergrad" if programme == "PG" else r"m\.?tech|postgrad", query, re.I)
        rule_ref = re.search(r"\b(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\b", query)
        n, k1, b = len(self.chunks), 1.4, 0.7
        scored: list[tuple[float, Chunk]] = []
        for c in self.chunks:
            if types and c.type not in types:
                continue
            tf: dict[str, int] = {}
            for t in c.tokens:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                df = self.df[t]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(c.tokens) / self.avg_len))
            if s <= 0:
                continue
            if rule_ref and c.rule and c.rule.startswith(rule_ref.group(1)):
                s *= 2
            if c.status == "circular-in-force":
                s *= 1.5
            if c.status == "superseded":
                s *= 0.35
            if c.non_binding:
                s *= 0.3
            if not asked_other and wrong_level.search(c.section):
                s *= 0.4
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:k]]

    @staticmethod
    def label(c: Chunk) -> str:
        label = {
            "circular-in-force": "CIRCULAR IN FORCE - overrides the handbook",
            "superseded": "SUPERSEDED EDITION - do not use unless asked about the old rule",
            "upcoming": "NOT YET IN EFFECT",
            "circular-upcoming": "NOT YET IN EFFECT",
        }.get(c.status, "current")
        if c.non_binding:
            label += " | NON-BINDING DRAFT/PROPOSAL - has NO effect"
        return label

    def listing(self, user: dict | None = None, role: str = "visitor", doc_type: str | None = None) -> list[dict]:
        """Document list enriched with trust status, relevance to this user and a 'new' flag.
        Visitors never see lesson notes (they are for enrolled students)."""
        status_text = {
            "circular-in-force": "circular in force (overrides the handbook)",
            "circular-upcoming": "circular not yet in effect",
            "current": "current",
            "superseded": "superseded by a newer edition",
            "upcoming": "not yet in effect",
        }
        out = []
        for d in self.docs:
            if role == "visitor" and d["type"] == "lesson_notes":
                continue
            if doc_type and d["type"] != doc_type:
                continue
            st = self.status.get(d["id"], "current")
            out.append({
                "id": d["id"],
                "title": d["title"],
                "type": d["type"],
                "version": d.get("version"),
                "effective_date": d.get("effective_date"),
                "status": status_text.get(st, st),
                "applies_to": d.get("applies_to"),
                "applies_to_you": applies_to_user(d.get("applies_to"), user) if user else None,
                "is_new": bool(d.get("is_surprise")) or d["id"] in self.new_ids,
                "download": f"/api/documents/{d['id']}/file",
            })
        return out

    def get_doc(self, doc_id: int) -> dict | None:
        return next((d for d in self.docs if d["id"] == doc_id), None)

    def summary(self) -> list[dict]:
        return [{"id": d["id"], "title": d["title"], "type": d["type"], "version": d.get("version"),
                 "effective_date": d.get("effective_date"), "status": self.status.get(d["id"], "unknown")} for d in self.docs]


kb = KnowledgeBase()
