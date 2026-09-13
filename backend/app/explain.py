"""On-demand rationale for a piece of code a viewer highlighted.

Unlike parser.py/miner.py's batched, once-per-analysis LLM calls, this
fires one call per "why is this used?" question asked while browsing a
file — interactive, not part of the background pipeline.
"""

from app.llm import call_llm, call_with_retry

CONTEXT_LINES = 15
MAX_SELECTION_CHARS = 4000
DEFAULT_QUESTION = "Why is this code here / what is it used for?"


def _extract_context(file_content: str, start_line: int, end_line: int) -> str:
    lines = file_content.splitlines()
    lo = max(0, start_line - 1 - CONTEXT_LINES)
    hi = min(len(lines), end_line + CONTEXT_LINES)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))


def _prompt(path: str, selected_text: str, context: str, question: str, file_summary: str | None) -> str:
    summary_line = f'This file\'s overall role: "{file_summary}"\n\n' if file_summary else ""
    return f"""A curious reader who has never seen this codebase and isn't a programmer
highlighted a specific piece of code inside one file and asked a question about it.
Answer about that highlighted part specifically, not the whole file.

Write in plain, everyday language, like explaining it to a friend. Avoid unexplained
jargon (e.g. "API", "middleware", "async"); if a technical term is essential, briefly
explain it in plain words right next to it.

File: {path}
{summary_line}Surrounding code, with line numbers, for context:
```
{context}
```

The reader highlighted exactly these lines:
```
{selected_text}
```

Their question: {question}

Answer in 2-4 plain-language sentences.
"""


def explain_selection(
    path: str,
    file_content: str,
    start_line: int,
    end_line: int,
    question: str | None = None,
    file_summary: str | None = None,
) -> str:
    lines = file_content.splitlines()
    selected_text = "\n".join(lines[start_line - 1 : end_line])[:MAX_SELECTION_CHARS]
    context = _extract_context(file_content, start_line, end_line)
    prompt = _prompt(path, selected_text, context, question or DEFAULT_QUESTION, file_summary)
    return call_with_retry(lambda: call_llm(prompt)).strip()
