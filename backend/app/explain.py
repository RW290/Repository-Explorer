"""On-demand rationale for a piece of code, a whole file, or the project as
a whole, answered on request rather than during the background pipeline.

Unlike parser.py/miner.py's batched, once-per-analysis LLM calls, each of
these fires one call per question asked while browsing — interactive, not
part of the pipeline.
"""

from app.audience import AUDIENCE_FRAMING
from app.llm import call_llm, call_with_retry

CONTEXT_LINES = 15
# Someone is waiting on these with the panel open, and they're 2-4 sentence
# answers grounded in text handed to the model — low reasoning effort roughly
# halves the wait without changing what gets said.
ANSWER_EFFORT = "low"
MAX_SELECTION_CHARS = 4000
DEFAULT_QUESTION = "Why is this code here / what is it used for?"
DEFAULT_FILE_QUESTION = "Why does this file exist / what is it necessary for in the wider project?"
DEFAULT_PROJECT_QUESTION = "What is this project, what does it do, and how does it broadly work?"


def _extract_context(file_content: str, start_line: int, end_line: int) -> str:
    lines = file_content.splitlines()
    lo = max(0, start_line - 1 - CONTEXT_LINES)
    hi = min(len(lines), end_line + CONTEXT_LINES)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))


def _prompt(
    path: str, selected_text: str, context: str, question: str, file_summary: str | None, dependencies: list[str] | None
) -> str:
    summary_line = f'This file\'s overall role: "{file_summary}"\n' if file_summary else ""
    deps_line = f"This file depends on: {', '.join(dependencies)}\n" if dependencies else ""
    return f"""A reader highlighted a specific piece of code inside one file and asked a
question about it. Answer about that highlighted part specifically, not the
whole file.

{AUDIENCE_FRAMING}

File: {path}
{summary_line}{deps_line}Surrounding code, with line numbers, for context:
```
{context}
```

The reader highlighted exactly this (it may be a fragment of a line rather than
whole lines — answer about this specific text, using the surrounding code only
as context):
```
{selected_text}
```

Their question: {question}

Answer in 2-4 sentences.
"""


def explain_selection(
    path: str,
    file_content: str,
    start_line: int,
    end_line: int,
    question: str | None = None,
    file_summary: str | None = None,
    dependencies: list[str] | None = None,
    selected_text: str | None = None,
) -> str:
    """`selected_text` is what the reader actually highlighted, which can be a
    fragment of a line rather than whole lines. Falling back to the full line
    range when it's absent keeps callers that only know a range working, but
    asks a blunter question than the reader intended."""
    lines = file_content.splitlines()
    selected_text = (selected_text or "\n".join(lines[start_line - 1 : end_line]))[:MAX_SELECTION_CHARS]
    context = _extract_context(file_content, start_line, end_line)
    prompt = _prompt(path, selected_text, context, question or DEFAULT_QUESTION, file_summary, dependencies)
    return call_with_retry(lambda: call_llm(prompt, think=ANSWER_EFFORT, max_tokens=1500)).strip()


def _file_prompt(path: str, file_summary: str, dependencies: list[str], dependents: list[str], question: str) -> str:
    deps_line = f"Files it depends on: {', '.join(dependencies)}\n" if dependencies else "Files it depends on: none recorded.\n"
    dependents_line = (
        f"Files that depend on it: {', '.join(dependents)}\n" if dependents else "Files that depend on it: none recorded.\n"
    )
    return f"""A reader is asking why one specific file exists and whether it's actually
necessary, in the context of the wider project it's part of.

{AUDIENCE_FRAMING}

File: {path}
This file's role: "{file_summary}"
{deps_line}{dependents_line}
Their question: {question}

Answer in 2-4 sentences, focused on why this file's existence and scope make
sense given what it depends on and what depends on it — a genuine
separation-of-concerns argument, not just what it does in isolation. If the
dependency/dependent lists are too thin to support that argument, say so
rather than inventing a rationale from nothing.
"""


def explain_file(
    path: str,
    file_summary: str,
    dependencies: list[str],
    dependents: list[str],
    question: str | None = None,
) -> str:
    prompt = _file_prompt(path, file_summary, dependencies, dependents, question or DEFAULT_FILE_QUESTION)
    return call_with_retry(lambda: call_llm(prompt, think=ANSWER_EFFORT, max_tokens=1500)).strip()


def _project_prompt(owner: str, name: str, overview: str, question: str) -> str:
    return f"""A reader is asking a follow-up question about the GitHub project
"{owner}/{name}", having already read this brief overview of it:

"{overview}"

{AUDIENCE_FRAMING}

Their question: {question}

Answer in 2-4 sentences.
"""


def explain_project(owner: str, name: str, overview: str, question: str | None = None) -> str:
    prompt = _project_prompt(owner, name, overview, question or DEFAULT_PROJECT_QUESTION)
    return call_with_retry(lambda: call_llm(prompt, think=ANSWER_EFFORT, max_tokens=1500)).strip()
