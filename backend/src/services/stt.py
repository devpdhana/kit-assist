"""Speech-to-text with failover: Groq Whisper -> Gemini audio understanding.
(The browser's Web Speech recognition API is deliberately NOT used.)"""

import base64
import logging
import re

import httpx

from src.configs.config import settings

log = logging.getLogger("stt")

PROMPT = (
    "Knotopian Institute of Technology, KIT, on-duty, OD, medical leave, personal leave, attendance, internal assessment, "
    "DBMS, CS304, B.Tech CSE, ECE, MECH, placement, Zoho, TCS, Freshworks, hostel fee."
)
HALLUCINATIONS = re.compile(r"^(thank you\.?|thanks for watching!?|you|bye\.?|\.+|subtitles by.*)$", re.I)


class STTError(Exception):
    pass


def clean_transcript(text: str | None) -> str:
    s = (text or "").strip()
    return "" if HALLUCINATIONS.match(s) else s


async def transcribe(audio: bytes, mime: str = "audio/webm") -> tuple[str, str]:
    """Returns (text, provider)."""
    errors: list[str] = []
    ext = "ogg" if "ogg" in mime else "mp4" if "mp4" in mime else "wav" if "wav" in mime else "webm"
    async with httpx.AsyncClient(timeout=15) as client:
        if settings.GROQ_API_KEY:
            try:
                res = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                    files={"file": (f"speech.{ext}", audio, mime)},
                    data={"model": settings.GROQ_STT_MODEL, "language": "en", "temperature": "0", "prompt": PROMPT, "response_format": "json"},
                )
                res.raise_for_status()
                return clean_transcript(res.json().get("text")), f"groq:{settings.GROQ_STT_MODEL}"
            except Exception as exc:
                errors.append(f"groq: {exc}")
                log.warning("Groq STT failed: %s", exc)
        if settings.GEMINI_API_KEY:
            try:
                res = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent",
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json={
                        "contents": [{"parts": [
                            {"inline_data": {"mime_type": mime.split(";")[0], "data": base64.b64encode(audio).decode()}},
                            {"text": f"Transcribe this speech exactly, in English. Output only the transcript. Context words: {PROMPT}"},
                        ]}],
                        "generationConfig": {"temperature": 0},
                    },
                )
                res.raise_for_status()
                parts = res.json()["candidates"][0]["content"]["parts"]
                return clean_transcript("".join(p.get("text", "") for p in parts)), f"gemini:{settings.GEMINI_MODEL}"
            except Exception as exc:
                errors.append(f"gemini: {exc}")
                log.warning("Gemini STT failed: %s", exc)
    raise STTError("; ".join(errors) or "no STT provider configured")
