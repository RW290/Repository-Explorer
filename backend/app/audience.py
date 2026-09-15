"""Shared framing for every LLM prompt in this app.

The reader is a software engineer using this tool to learn good coding
practice and system design — not a non-technical audience. Every prompt
that generates reader-facing text (file summaries, PR rationale, project
overview, and all three "ask why" variants) includes this framing, so the
answer explains the *why* behind a design choice — tradeoffs, patterns, and
quality-of-service implications (latency, scalability, coupling,
maintainability, failure modes) where genuinely relevant — instead of just
describing what code does.
"""

AUDIENCE_FRAMING = (
    "The reader is a software engineer using this tool to learn good coding "
    "practice and system design. Don't just describe what the code does — "
    "explain the design rationale behind it: why this approach was likely "
    "chosen over plausible alternatives, what tradeoffs it represents, and "
    "— where genuinely relevant — what quality-of-service implications it "
    "has (latency, scalability, coupling, maintainability, failure modes). "
    "Use precise technical language; don't simplify away real terms, but do "
    "explain why a term matters here if that's not obvious. If there isn't "
    "enough context to responsibly infer a design rationale, say what's "
    "actually knowable instead of guessing."
)
