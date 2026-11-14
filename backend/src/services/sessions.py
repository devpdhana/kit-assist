"""Server-side sessions. The browser only holds an opaque HMAC-signed session id
(httpOnly, SameSite=Strict cookie); the college bearer token never leaves the server."""

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field

from src.configs.config import settings

COOKIE = "kit_sid"
VISITOR_COOKIE = "kit_vid"


def sign(value: str) -> str:
    mac = hmac.new(settings.SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def unsign(signed: str | None) -> str | None:
    if not signed or "." not in signed:
        return None
    value = signed.rsplit(".", 1)[0]
    return value if hmac.compare_digest(sign(value), signed) else None


@dataclass
class Channel:
    """One assistant conversation (public page or logged-in page)."""

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    history: list = field(default_factory=list)

    def remember(self, user: str, assistant: str) -> None:
        self.history = (self.history + [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}])[-12:]


@dataclass
class Session:
    id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    visitor_id: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    auth: dict | None = None  # {token, user, expires_at}
    public: Channel = field(default_factory=Channel)
    private: Channel = field(default_factory=Channel)
    last_seen: float = field(default_factory=time.time)

    def owners(self) -> list[str]:
        o = [f"visitor:{self.visitor_id}"]
        if self.auth:
            o.insert(0, f"user:{self.auth['user']['login_id']}")
        return o


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, sid: str | None) -> Session | None:
        s = self._sessions.get(sid or "")
        if s and time.time() - s.last_seen > settings.SESSION_TTL_S:
            self._sessions.pop(s.id, None)
            return None
        if s:
            s.last_seen = time.time()
            if s.auth and s.auth["expires_at"] < time.time():
                s.auth, s.private = None, Channel()
        return s

    def create(self, visitor_id: str | None = None) -> Session:
        s = Session()
        if visitor_id:
            s.visitor_id = visitor_id
        self._sessions[s.id] = s
        self._sweep()
        return s

    def rotate(self, s: Session) -> None:
        """New session id on privilege change (prevents session fixation)."""
        self._sessions.pop(s.id, None)
        s.id = secrets.token_urlsafe(24)
        self._sessions[s.id] = s

    def _sweep(self) -> None:
        cutoff = time.time() - settings.SESSION_TTL_S
        for sid in [k for k, v in self._sessions.items() if v.last_seen < cutoff]:
            self._sessions.pop(sid, None)


store = SessionStore()
