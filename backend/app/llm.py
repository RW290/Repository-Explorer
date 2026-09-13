"""Single abstracted LLM interface: prompt in, text out.

Backend is an open-weight instruct model served by Hugging Face's hosted
Inference Providers (via huggingface_hub.InferenceClient) rather than
Gemini. The model still runs on someone else's infrastructure, not this
server and not the end user's device — a 7B-class model is too large to
run reliably in a browser (multi-GB download, needs WebGPU) and running it
locally on this server would make every concurrent analysis contend for
the same CPU/GPU. HF's router picks whichever backing provider currently
has the model warm.

Swapping backends later means changing call_llm's body, not any caller in
parser.py/miner.py.
"""

import os
import time
from collections.abc import Callable
from typing import TypeVar

from huggingface_hub import InferenceClient

from app.errors import LLMConfigError, LLMTransientError, PipelineError

# Confirmed working on HF's default (auto-selected) serverless Inference
# Provider routing as of writing — several other reasonable-looking open
# instruct models (Qwen2.5-7B-Instruct, Mistral-7B-Instruct-v0.3,
# Phi-3.5-mini-instruct, zephyr-7b-beta) currently 400/404 here because no
# enabled provider serves them on the free serverless tier, only on paid
# dedicated endpoints. If this one stops working, check
# huggingface.co/models?inference_provider=all&pipeline_tag=text-generation
# for a currently-served alternative before assuming the code is broken.
# Override via HF_MODEL without a code change.
DEFAULT_MODEL = os.environ.get("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")

T = TypeVar("T")


_client_instance: InferenceClient | None = None


def _client() -> InferenceClient:
    # Held singleton for the same reason the old Gemini client was: avoid
    # rebuilding (and the underlying HTTP session churn) on every call.
    global _client_instance
    if _client_instance is None:
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise LLMConfigError(
                "No HF_TOKEN is configured on this server, so file summaries and PR "
                "rationale can't be generated. Create a free token at "
                "huggingface.co/settings/tokens (read access is enough), add it as a "
                "secret, and restart. The fixture demo works without it."
            )
        _client_instance = InferenceClient(token=token)
    return _client_instance


def call_llm(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Send one prompt, return the raw text response."""
    try:
        response = _client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except PipelineError:
        raise
    except Exception as e:
        raise _translate_model_error(e, model) from None
    return response.choices[0].message.content or ""


def _translate_model_error(e: Exception, model: str) -> PipelineError:
    """Turn a raw huggingface_hub exception into something worth reading."""
    response = getattr(e, "response", None)
    status_code = getattr(response, "status_code", None)
    text = str(e)
    lowered = text.lower()

    if status_code == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return LLMTransientError(
            "Hugging Face is rate-limiting this server's requests (too many in a short "
            "window) and kept doing so after several automatic retries. Try again in a "
            "minute."
        )

    if status_code in (401, 403) or "unauthorized" in lowered or "authorization" in lowered:
        return LLMConfigError(
            "Hugging Face rejected this server's HF_TOKEN. Check that the token is "
            "correct, still active, and has inference permission at "
            "huggingface.co/settings/tokens, then update the secret."
        )

    if status_code == 404 or "not found" in lowered:
        return LLMConfigError(
            f"The Hugging Face model this server is configured to use ({model}) isn't "
            "available through Inference Providers right now. Check huggingface.co/models "
            "for a currently-served alternative and update the HF_MODEL secret."
        )

    if status_code == 503 or "loading" in lowered or "unavailable" in lowered or "overloaded" in lowered:
        return LLMTransientError(
            "The Hugging Face-hosted model is warming up or temporarily unavailable and "
            "kept failing after several automatic retries. This usually clears within a "
            "minute or two — try again shortly."
        )

    short = text.splitlines()[0][:200] if text else "no details given"
    return PipelineError(f"The AI model backend failed unexpectedly: {short}")


def call_with_retry(fn: Callable[[], T], max_retries: int = 5) -> T:
    """Retry-with-backoff wrapper for call_llm invocations.

    Only retries genuinely transient failures. A bad token or a retired
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
