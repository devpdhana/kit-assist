"""HTTP API for the React frontend."""

import logging
import re
import time
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.configs.config import settings
from src.services import history_store
from src.services.assistant_service import run_turn
from src.services.college_client import CollegeError, college
from src.services.knowledge_base import kb
from src.services.sessions import COOKIE, VISITOR_COOKIE, Channel, Session, sign, store, unsign
from src.services.stt import STTError, transcribe

log = logging.getLogger("api")
router = APIRouter(prefix="/api")

Mode = Literal["public", "private"]


# ---------------------------------------------------------------- deps
def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, sign(value), max_age=max_age, httponly=True, samesite="strict", secure=settings.IS_PROD, path="/")


def get_session(request: Request, response: Response) -> Session:
    s = store.get(unsign(request.cookies.get(COOKIE)))
    if s is None:
        s = store.create(unsign(request.cookies.get(VISITOR_COOKIE)))
        _set_cookie(response, COOKIE, s.id, settings.SESSION_TTL_S)
        _set_cookie(response, VISITOR_COOKIE, s.visitor_id, 180 * 86400)
    return s


def csrf_guard(request: Request) -> None:
    """Defence in depth on top of SameSite=Strict: state-changing calls need a custom header."""
    if request.method != "GET" and request.headers.get("x-requested-with") != "kit-assist":
        raise HTTPException(403, "Forbidden")


_buckets: dict[str, list[float]] = defaultdict(list)


def rate_limit(max_calls: int, window_s: int = 60):
    def dep(request: Request) -> None:
        key = f"{request.url.path}:{request.client.host if request.client else '?'}"
        now = time.time()
        _buckets[key] = [t for t in _buckets[key] if now - t < window_s] + [now]
        if len(_buckets[key]) > max_calls:
            raise HTTPException(429, "Too many requests. Please slow down.")

    return dep


def public_user(u: dict) -> dict:
    keys = ("login_id", "full_name", "role", "department", "class_code", "programme", "year_of_study", "notes")
    return {k: u.get(k) for k in keys}


# ---------------------------------------------------------------- models
class LoginIn(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    mode: Mode = "public"


class ModeIn(BaseModel):
    mode: Mode = "public"


# ---------------------------------------------------------------- health / public
@router.get("/health")
async def health():
    try:
        await college.health()
        college_ok = True
    except Exception:
        college_ok = False
    return {"ok": True, "college": college_ok, "llm_configured": settings.has_llm,
            "documents": {"count": len(kb.docs), "chunks": len(kb.chunks), "last_sync": kb.last_sync, "error": kb.last_error}}


@router.get("/public/info")
async def public_info():
    try:
        profile, courses, today = await college.profile(), await college.courses(), await college.today()
    except CollegeError:
        raise HTTPException(502, "College system unavailable")
    return {"profile": profile, "courses": courses, "today": today, "documents": kb.listing(None, "visitor")}


# ---------------------------------------------------------------- documents
def _can_see(doc: dict, s: Session) -> bool:
    """Lesson notes are for logged-in users only; everything else is public."""
    return doc["type"] != "lesson_notes" or s.auth is not None


@router.get("/documents")
async def documents(s: Session = Depends(get_session)):
    user = s.auth["user"] if s.auth else None
    return {"documents": kb.listing(user, user["role"] if user else "visitor"), "last_sync": kb.last_sync}


@router.get("/documents/{doc_id}/file", dependencies=[Depends(rate_limit(30))])
async def document_file(doc_id: int, s: Session = Depends(get_session)):
    """Proxy the PDF so the participant key never reaches the browser."""
    doc = kb.get_doc(doc_id)
    if not doc or not _can_see(doc, s):
        raise HTTPException(404, "Not found")
    try:
        pdf = await college.document_file(doc_id)
    except CollegeError:
        raise HTTPException(502, "College system unavailable")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", doc["title"])[:80] or f"document_{doc_id}"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe}.pdf"', "Cache-Control": "private, max-age=300"},
    )


# ---------------------------------------------------------------- auth
@router.post("/auth/login", dependencies=[Depends(csrf_guard), Depends(rate_limit(10))])
async def login(body: LoginIn, response: Response, s: Session = Depends(get_session)):
    try:
        r = await college.login(body.login_id.strip().lower(), body.password)
    except CollegeError as exc:
        if exc.status in (400, 401, 403, 422):
            raise HTTPException(401, "Invalid login ID or password.")
        raise HTTPException(502, "College system unavailable, try again.")
    store.rotate(s)
    _set_cookie(response, COOKIE, s.id, settings.SESSION_TTL_S)
    s.auth = {"token": r["access_token"], "user": r["user"], "expires_at": time.time() + min(r.get("expires_in", 28800), settings.SESSION_TTL_S)}
    s.private = Channel()
    return {"user": public_user(r["user"])}


@router.post("/auth/logout", dependencies=[Depends(csrf_guard)])
async def logout(response: Response, s: Session = Depends(get_session)):
    s.auth, s.private = None, Channel()
    store.rotate(s)
    _set_cookie(response, COOKIE, s.id, settings.SESSION_TTL_S)
    return {"ok": True}


@router.get("/me")
async def me(s: Session = Depends(get_session)):
    return {"user": public_user(s.auth["user"]) if s.auth else None}


# ---------------------------------------------------------------- conversation
async def _converse(s: Session, mode: Mode, text: str, stt: dict | None = None) -> dict:
    if mode == "private" and not s.auth:
        raise HTTPException(401, "Please log in first.")
    private = mode == "private"
    ch = s.private if private else s.public
    role = s.auth["user"]["role"] if private else "visitor"
    out = await run_turn(
        thread_id=f"{mode}:{ch.conversation_id}",
        text=text,
        role=role,
        user=s.auth["user"] if private else None,
        token=s.auth["token"] if private else None,
        history=list(ch.history),
    )
    ch.remember(text, out["reply"])
    owner = f"user:{s.auth['user']['login_id']}" if private else f"visitor:{s.visitor_id}"
    try:
        history_store.save_turn(ch.conversation_id, owner, role, text, out["reply"],
                                {"trace": out["trace"], "evaluation": out["evaluation"], "latency_ms": out["latency_ms"], "stt": stt})
    except Exception as exc:
        log.error("history save failed: %s", exc)
    return {**out, "conversation_id": ch.conversation_id}


@router.post("/chat", dependencies=[Depends(csrf_guard), Depends(rate_limit(40))])
async def chat(body: ChatIn, s: Session = Depends(get_session)):
    return await _converse(s, body.mode, body.text.strip())


@router.post("/voice", dependencies=[Depends(csrf_guard), Depends(rate_limit(40))])
async def voice(request: Request, mode: Mode = "public", s: Session = Depends(get_session)):
    audio = await request.body()
    if len(audio) < 800:
        raise HTTPException(400, "No audio received.")
    if len(audio) > 6 * 1024 * 1024:
        raise HTTPException(413, "Recording too long.")
    t0 = time.perf_counter()
    try:
        text, provider = await transcribe(audio, request.headers.get("content-type", "audio/webm"))
    except STTError as exc:
        log.warning("STT failed: %s", exc)
        raise HTTPException(503, "Speech recognition is unavailable right now. Please type your question.")
    stt = {"provider": provider, "ms": round((time.perf_counter() - t0) * 1000)}
    if not text:
        return {"transcript": "", "reply": "Sorry, I didn't catch that. Could you say it again?", "trace": [], "stt": stt}
    return {"transcript": text, "stt": stt, **(await _converse(s, mode, text, stt))}


@router.post("/conversation/new", dependencies=[Depends(csrf_guard)])
async def new_conversation(body: ModeIn, s: Session = Depends(get_session)):
    ch = Channel()
    if body.mode == "private":
        s.private = ch
    else:
        s.public = ch
    return {"conversation_id": ch.conversation_id}


# ---------------------------------------------------------------- history
@router.get("/history")
async def history(s: Session = Depends(get_session)):
    return {"conversations": history_store.list_conversations(s.owners())}


@router.get("/history/{cid}")
async def history_item(cid: str, s: Session = Depends(get_session)):
    conv = history_store.get_conversation(s.owners(), cid)
    if not conv:
        raise HTTPException(404, "Not found")
    return conv


@router.delete("/history/{cid}", dependencies=[Depends(csrf_guard)])
async def history_delete(cid: str, s: Session = Depends(get_session)):
    if not history_store.delete_conversation(s.owners(), cid):
        raise HTTPException(404, "Not found")
    return {"ok": True}
