"""LLM factory with provider failover, built on LangChain `with_fallbacks`.

    Groq model A -> Groq model B -> Groq model C -> Gemini

* The Groq models rotate between calls so their separate free-tier token
  quotas add up (we hit per-model TPM limits in load tests otherwise).
* `max_retries=0` + short timeouts: a rate-limited provider fails fast and the
  next one answers, instead of the SDK silently retrying for seconds.
* Each agent makes single-shot structured calls (no cross-provider tool-call
  history), so any provider can pick up any step.
"""

import itertools
import logging
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from src.configs.config import settings

log = logging.getLogger("llm")
T = TypeVar("T", bound=BaseModel)
_rotation = itertools.count()


def _groq(model: str) -> BaseChatModel:
    from langchain_groq import ChatGroq

    extra = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
    return ChatGroq(model=model, temperature=0, max_retries=0, timeout=12, api_key=settings.GROQ_API_KEY, **extra)


def _gemini() -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL, temperature=0, max_retries=0, timeout=20, google_api_key=settings.GEMINI_API_KEY)


def _models(tier: str) -> list[BaseChatModel]:
    models: list[BaseChatModel] = []
    if settings.GROQ_API_KEY:
        names = settings.GROQ_FAST_MODELS if tier == "fast" else settings.GROQ_SMART_MODELS
        start = next(_rotation) % len(names)
        models += [_groq(n) for n in names[start:] + names[:start]]
    if settings.GEMINI_API_KEY:
        models.append(_gemini())
    return models


def structured(schema: type[T], tier: str = "fast") -> Runnable:
    """Runnable returning `schema`, failing over across every configured provider."""
    models = _models(tier)
    if not models:
        raise RuntimeError("No LLM provider configured")
    runnables = [m.with_structured_output(schema) for m in models]
    return runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]


def chat(tier: str = "smart") -> Runnable:
    """Plain chat runnable (string/messages in, AIMessage out) with failover."""
    models = _models(tier)
    if not models:
        raise RuntimeError("No LLM provider configured")
    return models[0].with_fallbacks(models[1:]) if len(models) > 1 else models[0]


def provider_name(model: BaseChatModel | None) -> str:
    return getattr(model, "model_name", None) or getattr(model, "model", "") or "unknown"
