"""FastAPI entrypoint: API + the built React app (one service, one URL)."""

import asyncio
import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import router
from src.configs.config import settings
from src.services import history_store
from src.services.college_client import college
from src.services.knowledge_base import kb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main")


async def _refresh_docs() -> None:
    while True:  # live document sync: new circulars appear without a redeploy
        await kb.sync()
        await asyncio.sleep(settings.DOCS_REFRESH_S)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.COLLEGE_API_KEY:
        log.warning("COLLEGE_API_KEY is not set")
    if not settings.has_llm:
        log.warning("No AI keys set - running on rule-based agents only")
    history_store.init()
    task = asyncio.create_task(_refresh_docs())
    yield
    task.cancel()
    await college.aclose()


app = FastAPI(title="KIT Assist", version="2.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(router)

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        if k == "Content-Security-Policy" and request.url.path.startswith("/api/docs"):
            continue  # Swagger UI needs its CDN assets
        response.headers.setdefault(k, v)
    if settings.IS_PROD:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse({"detail": "Something went wrong."}, status_code=500)


# ---- React SPA ----
dist = settings.FRONTEND_DIST
if (dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    file = (dist / path).resolve()
    if path and file.is_file() and dist.resolve() in file.parents:
        return FileResponse(file)
    index = dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=404)
