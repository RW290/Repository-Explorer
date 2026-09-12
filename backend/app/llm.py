"""Single abstracted LLM interface: prompt in, text out.

Default backend is Gemini Flash on the free tier (cost constraint: this must
not cost money to run). Swapping backends later means changing call_llm's
body, not any caller in parser.py/miner.py.
"""

import os
import time
from collections.abc import Callable
from typing import TypeVar

from google import genai

from app.errors import LLMConfigError, LLMQuotaError, LLMTransientError, PipelineError

_ALLOWED_MODEL_SUBSTRING = "flash"
DEFAULT_MODEL = "gemini-3.6-flash"

T = TypeVar("T")


_client_instance: genai.Client | None = None


def _client() -> genai.Client:
    # Must be a held singleton, not a fresh instance per call — a throwaway
    # genai.Client gets garbage-collected mid-request (its httpx client closes
    # under it), which surfaces as "Cannot send a request, as the client has
    # been closed."
    global _client_instance
    if _client_instance is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMConfigError(
                "No GEMINI_API_KEY is configured on this server, so file summaries and "
                "PR rationale can't be generated. Add it as a secret (get a free key at "
                "aistudio.google.com) and restart. The fixture demo works without it."
            )
        _client_instance = genai.Client(api_key=api_key)
    return _client_instance


def call_llm(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Send one prompt, return the raw text response.

    Pinned to a specific version rather than "gemini-flash-latest": that
    alias silently resolved to a preview model (gemini-3.8-flash) with a
    much stricter free-tier quota (20 requests/day, vs. the usual
    per-minute rate limit) — confirm the current recommended alias at
    aistudio.google.com before changing this, per the build brief's own
    warning that Google renames/versions these periodically.

    Guards against ever pointing this at a Pro (billed) model — that's the
    one thing that turns this tool from free to billed.
    """
    if _ALLOWED_MODEL_SUBSTRING not in model.lower():
        raise LLMConfigError(
            f"Refusing to call the model {model!r}: only Gemini Flash variants are "
            "allowed, because a Pro model would make this tool cost money to run."
        )
    try:
        response = _client().models.generate_content(model=model, contents=prompt)
    except PipelineError:
        raise
    except Exception as e:
        raise _translate_model_error(e, model) from None
    return response.text


def _translate_model_error(e: Exception, model: str) -> PipelineError:
    """Turn a raw google-genai exception into something worth reading.

    The daily-vs-per-minute distinction matters: both arrive as 429s, but one
    clears in seconds and the other needs to wait for a day boundary, so only
    the first is worth retrying.
    """
    text = str(e)

    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        if "PerDay" in text or "per day" in text.lower():
            return LLMQuotaError(
                "This server has used up its free Gemini quota for the day. The free tier "
                "allows only a small number of requests per day, and analyzing one new "
                "repository uses several. Repositories analyzed earlier still load "
                "instantly from the cache; fresh analysis will work again after the quota "
                "resets (within 24 hours)."
            )
        return LLMTransientError(
            "The AI service is rate-limiting this server (too many requests in a short "
            "window) and kept doing so after several automatic retries. Try again in a minute."
        )

    if "API key not valid" in text or "API_KEY_INVALID" in text or "PERMISSION_DENIED" in text:
        return LLMConfigError(
            "Google rejected this server's GEMINI_API_KEY. Check that the key is correct "
            "and still active at aistudio.google.com, then update the secret."
        )

    if "NOT_FOUND" in text and "model" in text.lower():
        return LLMConfigError(
            f"The Gemini model this server is configured to use ({model}) is no longer "
            "available. Google retires and renames these periodically — check "
            "aistudio.google.com for the current Flash model and update DEFAULT_MODEL in "
            "backend/app/llm.py."
        )

    if "503" in text or "UNAVAILABLE" in text:
        return LLMTransientError(
            "Google's Gemini service is temporarily overloaded and kept failing after "
            "several automatic retries. This usually clears on its own — try again shortly."
        )

    short = text.splitlines()[0][:200] if text else "no details given"
    return PipelineError(f"The AI model backend failed unexpectedly: {short}")


def call_with_retry(fn: Callable[[], T], max_retries: int = 5) -> T:
    """Retry-with-backoff wrapper for call_llm invocations.

    Only retries genuinely transient failures. A bad API key, a retired
    model, or an exhausted daily quota won't fix themselves within a backoff
    window, so those surface immediately with an explanation instead of
    burning five attempts first.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except LLMTransientError:
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")
