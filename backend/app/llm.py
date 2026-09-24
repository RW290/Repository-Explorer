"""Single abstracted LLM interface: prompt in, text out.

The model is an open-weight reasoning model served by Ollama Cloud
(ollama.com) — Ollama's hosted GPU service, not a model run on this server or
in the reader's browser. Someone else's infrastructure does the compute, so a
slow model costs this server nothing but latency. Free-tier usage is metered
by GPU time and resets every few hours, with a weekly cap, which suits a
low-traffic deployment.

Everything else talks to the model through call_llm, so changing provider
means changing its body and nothing in the callers.
"""

import os
import threading
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from ollama import Client

from app.errors import LLMConfigError, LLMTransientError, PipelineError

OLLAMA_HOST = "https://ollama.com"

# gpt-oss:20b is the smallest model in Ollama Cloud's free tier. The quota is
# metered by GPU time, so the smallest model that is good enough stretches it
# furthest. Override with OLLAMA_MODEL; see ollama.com/models for the catalog.
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:20b")

T = TypeVar("T")

# Responses are streamed, and these timeouts are why. A non-streaming call
# sends nothing for the whole generation — 30 to 300 seconds of a silent
# socket — and a connection idle that long can be dropped by a load balancer
# or NAT along the path without either end being told. With no read timeout
# the client then waits forever, and because call slots are finite, a few
# such dead connections stall the whole pipeline. Streaming keeps bytes
# moving so idle timers never fire, and makes a dead connection observable:
# no bytes for READ_STALL_SECONDS means it is gone, so give up and retry.
#
# The stall window has to cover *queue wait*, not just gaps mid-answer. With
# several calls in flight the provider queues some, and a queued request
# legitimately receives nothing until its turn. A window shorter than that
# wait kills requests that are merely queued and sends each retry to the back
# of the queue, making runs slower rather than safer. So: long enough to
# outlast the queue at our own concurrency, and a concurrency low enough
# (below) that the queue stays short.
READ_STALL_SECONDS = 240
CALL_DEADLINE_SECONDS = 300
# A reasoning model occasionally falls into a repetition loop and generates
# until it exhausts its context, which can run for many minutes and burns
# quota the whole time. It is a sampling accident — the same prompt completes
# normally on another attempt — so the answer is a guard, not a prompt fix. Callers say roughly how long a good
# answer is (`max_tokens`); that becomes a server-side `num_predict` cap,
# which stops the GPU burning quota, plus a client-side character ceiling in
# case the cap isn't honoured. Hitting either is a retryable failure: a fresh
# sample is almost always fine.
DEFAULT_MAX_TOKENS = 6000
_CHARS_PER_TOKEN_CEILING = 6
_TIMEOUT = httpx.Timeout(connect=15, read=READ_STALL_SECONDS, write=30, pool=30)

# How many LLM calls may be in flight at once, process-wide. On the free
# tier, four concurrent summary batches finish in about 122s against about
# 160s back-to-back: the provider mostly queues rather than parallelizes, so
# there is perhaps 1.5x to be had and no more. Two captures most of that
# while keeping any one request's queue wait to about one call's duration;
# more just lengthens the queue (see READ_STALL_SECONDS). It's a global
# semaphore rather than a per-pipeline pool so two people analyzing at once
# share the same ceiling instead of doubling it.
LLM_CONCURRENCY = max(1, int(os.environ.get("LLM_CONCURRENCY", "2")))
_slots = threading.BoundedSemaphore(LLM_CONCURRENCY)

# Set to False the first time the configured model rejects the `think`
# option, so a non-reasoning model costs one failed call, not one per call.
_think_supported = True


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
        _client_instance = Client(host=OLLAMA_HOST, headers={"Authorization": f"Bearer {api_key}"}, timeout=_TIMEOUT)
    return _client_instance


def call_llm(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    think: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    global _think_supported
    # `max_tokens` bounds reasoning + answer together (see the runaway note
    # above). Generous is fine — it is a fuse, not a target.
    options: dict = {"num_predict": max_tokens}
    if temperature is not None:
        options["temperature"] = temperature
    kwargs = {"think": think} if think and _think_supported else {}
    messages = [{"role": "user", "content": prompt}]
    ceiling = max_tokens * _CHARS_PER_TOKEN_CEILING
    with _slots:
        try:
            try:
                return _stream_to_text(_client().chat(model, messages=messages, stream=True, options=options, **kwargs), ceiling)
            except Exception as e:
                # A model without a reasoning mode rejects the option outright.
                if not kwargs or "think" not in str(e).lower():
                    raise
                _think_supported = False
                return _stream_to_text(_client().chat(model, messages=messages, stream=True, options=options), ceiling)
        except PipelineError:
            raise
        except Exception as e:
            raise _translate_model_error(e, model) from None


def _stream_to_text(chunks, ceiling: int) -> str:
    started = time.time()
    parts: list[str] = []
    seen = 0
    for chunk in chunks:
        message = chunk["message"]
        content = message.get("content") or ""
        parts.append(content)
        seen += len(content) + len(message.get("thinking") or "")
        if seen > ceiling:
            close = getattr(chunks, "close", None)
            if close:
                close()  # drop the connection so the server stops generating
            raise LLMTransientError(
                "The AI model got stuck repeating itself instead of finishing its answer, and kept "
                "doing so after several automatic retries. Try again shortly."
            )
        if time.time() - started > CALL_DEADLINE_SECONDS:
            raise LLMTransientError(
                f"The AI model backend was still generating after {CALL_DEADLINE_SECONDS // 60} minutes, "
                "which means something is wrong on its side. Try again shortly."
            )
    return "".join(parts)


def _translate_model_error(e: Exception, model: str) -> PipelineError:
    status_code = getattr(e, "status_code", None)
    text = str(e)
    lowered = text.lower()

    # A stalled or dropped connection (see the streaming note above) is the
    # textbook transient failure: nothing is wrong with the request, and the
    # same call usually succeeds on a fresh connection.
    if isinstance(e, (httpx.TimeoutException, httpx.TransportError)):
        return LLMTransientError(
            "The connection to the AI model backend went silent mid-answer and kept doing so after "
            "several automatic retries. This is usually a network or provider hiccup — try again shortly."
        )

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
    for attempt in range(max_retries):
        try:
            return fn()
        except LLMTransientError:
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")
