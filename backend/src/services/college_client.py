"""Async client for the KIT college system.

* The participant key is injected server-side only - never sent to the browser.
* Timeouts on every call; one retry on network errors / 5xx.
* Every POST carries an Idempotency-Key, so retries never double-submit.
* Short-lived cache for public data, served stale if the college system blips.
"""

import time
import uuid
from typing import Any

import httpx

from src.configs.config import settings


class CollegeError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    @property
    def retryable(self) -> bool:
        return self.status >= 500 or self.status in (408, 429)

    def to_dict(self) -> dict:
        return {"status": self.status, "code": self.code, "message": self.message, "field": self.field}


class CollegeClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=settings.COLLEGE_API_BASE, timeout=settings.COLLEGE_TIMEOUT_S)
        self._cache: dict[str, tuple[float, Any]] = {}

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
        raw: bool = False,
    ) -> Any:
        headers = {"X-Participant-Key": settings.COLLEGE_API_KEY, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if method != "GET":
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}

        last: Exception | None = None
        for attempt in range(2):  # POST retry is safe thanks to the Idempotency-Key
            try:
                res = await self._client.request(method, path, headers=headers, params=params, json=json)
            except httpx.HTTPError as exc:
                last = exc
                continue
            if res.is_success:
                return res.content if raw else (res.json() if res.content else None)
            try:
                body = res.json()
            except ValueError:
                body = {}
            err = body.get("error") or {}
            detail = body.get("detail")
            if isinstance(detail, list):
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            error = CollegeError(res.status_code, err.get("code", "college_error"), err.get("message") or detail or res.reason_phrase, err.get("field"))
            if error.retryable and attempt == 0:
                last = error
                continue
            raise error
        if isinstance(last, CollegeError):
            raise last
        raise CollegeError(503, "college_unreachable", f"College system unreachable: {last}")

    async def cached(self, key: str, path: str, ttl: float = 60) -> Any:
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            value = await self.request("GET", path)
        except CollegeError:
            if hit:
                return hit[1]  # stale is better than nothing
            raise
        self._cache[key] = (time.time(), value)
        return value

    # ---- public ----
    async def today(self) -> dict:
        return await self.cached("today", "/v1/today", ttl=30)

    async def profile(self) -> dict:
        return await self.cached("college", "/v1/college")

    async def courses(self) -> list:
        return await self.cached("courses", "/v1/courses")

    async def documents(self) -> list:
        return await self.request("GET", "/v1/documents")

    async def document_file(self, doc_id: int) -> bytes:
        return await self.request("GET", f"/v1/documents/{int(doc_id)}/file", raw=True)

    async def health(self) -> Any:
        return await self.request("GET", "/v1/health")

    async def login(self, login_id: str, password: str) -> dict:
        return await self.request("POST", "/v1/auth/login", json={"login_id": login_id, "password": password})

    async def aclose(self) -> None:
        await self._client.aclose()


college = CollegeClient()
