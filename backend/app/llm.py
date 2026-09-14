"""Single abstracted LLM interface: prompt in, text out.

Backend is an open-weight instruct model served by Ollama Cloud
(ollama.com) — Ollama's own hosted GPU service, not a model run locally on
this server or in the end user's device/browser. Same tradeoff as the
Hugging Face backend this replaced: someone else's infrastructure does the
compute, so a slow/expensive model here doesn't cost this server anything
but latency. Free-tier usage is quota'd by GPU-time and resets every few
hours plus a weekly cap, rather than Hugging Face's flat ~$0.10/month
credit — more forgiving for a low-traffic personal deployment.

Swapping backends later means changing call_llm's body, not any caller in
parser.py/miner.py.
"""

import os
import time
from collections.abc import Callable
from typing import TypeVar

from ollama import Client

from app.errors import LLMConfigError, LLMTransientError, PipelineError

OLLAMA_HOST = "https://ollama.com"

# gpt-oss:20b: confirmed working against the free tier as of writing, and
# the smallest of the free-tier catalog (gemma4:31b, gpt-oss:120b,
# nemotron-3-nano:30b, nemotron-3-super, nemotron-3-ultra are the other
# options) — since free-tier quota is GPU-time-based, the smallest model
# that's good enough stretches the quota furthest. Override via
# OLLAMA_MODEL without a code change.
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:20b")

T = TypeVar("T")


_client_instance: Client | None = None


def _client() -> Client:
    # Held singleton for the same reason the prior backends' clients were:
    # avoid rebuilding (and the underlying HTTP session churn) on every call.
    global _client_instance
    if _client_instance is None:
        api_key = os.environ.get("OLLAMA_API_KEY")
        if not api_key:
            raise LLMConfigError(
                "No OLLAMA_API_KEY is configured on this server, so file summaries and PR "
                "rationale can't be generated. Create a free key at "
                "ollama.com/settings/keys, add it as a secret, and restart. The fixture "
                "demo works without it."
            )
        _client_instance = Client(host=OLLAMA_HOST, headers={"Authorization": f"Bearer {api_key}"})
    return _client_instance


def call_llm(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Send one prompt, return the raw text response."""
    try:
        response = _client().chat(model, messages=[{"role": "user", "content": prompt}], stream=False)
    except PipelineError:
        raise
    except Exception as e:
        raise _translate_model_error(e, model) from None
    return response["message"]["content"] or ""


def _translate_model_error(e: Exception, model: str) -> PipelineError:
    """Turn a raw ollama-python exception into something worth reading."""
    status_code = getattr(e, "status_code", None)
    text = str(e)
    lowered = text.lower()

    if status_code == 429 or "rate limit" in lowered or "quota" in lowered:
        return LLMTransientError(
            "This server's Ollama Cloud free-tier usage quota is used up for now. Unlike "
            "a monthly credit, free-tier limits here reset every few hours (plus a weekly "
            "cap) — try again shortly, or upgrade at ollama.com if this keeps happening."
        )

    if status_code in (401, 403) or "unauthorized" in lowered:
        return LLMConfigError(
            "Ollama Cloud rejected this server's OLLAMA_API_KEY. Check that it's correct "
            "and still active at ollama.com/settings/keys, then update the secret."
        )

    if status_code == 404 or "not found" in lowered:
        return LLMConfigError(
            f"The Ollama Cloud model this server is configured to use ({model}) isn't "
            "available. Check ollama.com/models for a currently-served free-tier "
            "alternative and update the OLLAMA_MODEL secret."
        )

    if status_code == 503 or "unavailable" in lowered or "overloaded" in lowered:
        return LLMTransientError(
            "Ollama Cloud is temporarily overloaded and kept failing after several "
            "automatic retries. Try again shortly."
        )

    short = text.splitlines()[0][:200] if text else "no details given"
    return PipelineError(f"The AI model backend failed unexpectedly: {short}")


def call_with_retry(fn: Callable[[], T], max_retries: int = 5) -> T:
    """Retry-with-backoff wrapper for call_llm invocations.

    Only retries genuinely transient failures. A bad key or a retired
    model won't fix themselves within a backoff window, so those surface
    immediately with an explanation instead of burning five attempts first.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except LLMTransientError:
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")
