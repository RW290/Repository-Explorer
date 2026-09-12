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


def _client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


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


def call_with_retry(fn: Callable[[], T], max_retries: int = 5) -> T:
    """Retry-with-backoff wrapper for call_llm invocations.

    Flash's free tier is roughly 15 requests/minute; back off on 429s
    instead of failing the whole batch.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                raise
    raise RuntimeError("unreachable")
