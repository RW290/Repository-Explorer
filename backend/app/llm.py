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

_ALLOWED_MODEL_SUBSTRING = "flash"

T = TypeVar("T")


_client_instance: genai.Client | None = None


def _client() -> genai.Client:
    # Must be a held singleton, not a fresh instance per call — a throwaway
    # genai.Client gets garbage-collected mid-request (its httpx client closes
    # under it), which surfaces as "Cannot send a request, as the client has
    # been closed."
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client_instance


def call_llm(prompt: str, model: str = "gemini-flash-latest") -> str:
    """Send one prompt, return the raw text response.

    Guards against ever pointing this at a Pro (billed) model — that's the
    one thing that turns this tool from free to billed.
    """
    if _ALLOWED_MODEL_SUBSTRING not in model.lower():
        raise ValueError(
            f"Refusing to call non-Flash model {model!r}. Only Gemini Flash "
            "variants are permitted to keep this tool free to run."
        )
    response = _client().models.generate_content(model=model, contents=prompt)
    return response.text


_RETRYABLE_MARKERS = ("429", "503", "UNAVAILABLE")


def call_with_retry(fn: Callable[[], T], max_retries: int = 5) -> T:
    """Retry-with-backoff wrapper for call_llm invocations.

    Flash's free tier is roughly 15 requests/minute (429s); the API also
    occasionally returns transient 503s under general load. Back off on
    either instead of failing the whole batch.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if any(m in str(e) for m in _RETRYABLE_MARKERS) and attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                raise
    raise RuntimeError("unreachable")
