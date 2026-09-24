"""On-demand rationale for a piece of code, a whole file, the project as a
whole, or its architecture map, answered on request rather than during the
background pipeline.

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
DEFAULT_ARCHITECTURE_QUESTION = "Walk me through this architecture: what are the layers, and how does work flow between them?"
DEFAULT_FOCUS_QUESTION = "Explain this part of the architecture: what is it responsible for, and how does it relate to the rest?"
MAP_SUMMARY_CHARS = 170


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


# --- Architecture map ------------------------------------------------------


def _first_sentence(text: str, limit: int = MAP_SUMMARY_CHARS) -> str:
    flat = " ".join((text or "").split())
    for stop in (". ", "? ", "! "):
        cut = flat.find(stop)
        if 0 < cut < limit:
            return flat[: cut + 1]
    return flat[:limit] + ("…" if len(flat) > limit else "")


def describe_map(graph: dict) -> str:
    architecture = graph.get("architecture") or {}
    by_id = {n["id"]: n for n in graph.get("nodes", [])}

    def name(node_id: str) -> str:
        member = next((m for m in architecture.get("nodes", []) if m["id"] == node_id), None)
        if member and member.get("external"):
            return f"{member.get('label') or node_id} (external)"
        return node_id

    def member_line(member: dict) -> str:
        if member.get("external"):
            return f"  - {member.get('label') or member['id']} — EXTERNAL system, outside this repo. {member.get('description') or ''}".rstrip()
        node = by_id.get(member["id"], {})
        kind = "folder" if node.get("type") == "folder" else "file"
        return f"  - {member['id']} ({kind}) — {_first_sentence(node.get('summary', ''))}"

    lines: list[str] = []
    grouped: set[str] = set()
    for group in architecture.get("groups", []):
        members = [m for m in architecture.get("nodes", []) if m.get("group") == group["id"]]
        if not members:
            continue
        lines.append(f"Group \"{group['label']}\"" + (f" — {group['description']}" if group.get("description") else ""))
        for member in members:
            grouped.add(member["id"])
            lines.append(member_line(member))
    loose = [m for m in architecture.get("nodes", []) if m["id"] not in grouped]
    if loose:
        lines.append("Not in any group:")
        lines.extend(member_line(m) for m in loose)
    lines.append("")
    lines.append("Flows (arrow = direction drawn on the map):")
    for edge in architecture.get("edges", []):
        status = "import-verified" if edge.get("backed") else "INFERRED by the model, no import shows it"
        label = f": {edge['label']}" if edge.get("label") else ""
        lines.append(f"  - {name(edge['source'])} → {name(edge['target'])}{label} [{status}]")
    return "\n".join(lines)


def _focus_block(graph: dict, focus_kind: str | None, focus_id: str | None) -> tuple[str, str | None]:
    architecture = graph.get("architecture") or {}
    if focus_kind == "group":
        group = next((g for g in architecture.get("groups", []) if g["id"] == focus_id), None)
        if group:
            return (
                f"The reader has selected the group \"{group['label']}\" on the map. \"This section\", \"this part\" "
                "or \"this\" in their question means that group: answer about it specifically — what it is "
                "responsible for, why these files belong together, and how it connects to the other groups.\n\n",
                group["label"],
            )
    if focus_kind == "node":
        member = next((m for m in architecture.get("nodes", []) if m["id"] == focus_id), None)
        if member:
            if member.get("external"):
                label = member.get("label") or member["id"]
                return (
                    f"The reader has selected the external system \"{label}\" on the map. \"This\" in their "
                    "question means that system and the role it plays for this repository.\n\n",
                    label,
                )
            node = next((n for n in graph.get("nodes", []) if n["id"] == focus_id), {})
            return (
                f"The reader has selected `{focus_id}` on the map. \"This\", \"this file\" or \"this part\" in their "
                f"question means it. Its full summary: \"{node.get('summary', '')}\"\n\n",
                focus_id,
            )
    return "", None


def _architecture_prompt(owner: str, name: str, graph: dict, question: str, focus_text: str) -> str:
    overview = graph.get("overview") or ""
    overview_block = f'Project overview: "{overview}"\n\n' if overview else ""
    return f"""A reader is looking at an architecture map of the GitHub project "{owner}/{name}"
and has a question. The map groups the repository's real files into semantic
layers and draws the main flows between them.

{AUDIENCE_FRAMING}

{overview_block}The map they are looking at:

{describe_map(graph)}

{focus_text}Their question: {question}

How to answer:
- Ground the answer in THIS map. Name the actual groups and files involved
  (in backticks) and trace the actual flows, rather than describing
  architectures in general. Name the few files that carry the point; never
  recite a group's whole member list, which the reader can already see.
- If the question is about a general concept (a pattern, a protocol, a term
  such as REST, RPC, middleware, an adapter), explain the concept plainly
  first, then say where it does or does not show up in this repository.
- If the question rests on a mix-up — comparing two things that are not the
  same kind of thing, or assuming something the map does not show — say so
  kindly and explain the distinction; that correction is usually the most
  useful part of the answer.
- A flow marked INFERRED is the model's own claim, not something the code
  proves. If your answer leans on one, say that it is inferred.
- If the map does not contain enough to answer, say what it does show and
  what you would need to look at to know more. Do not invent files or flows.

Answer in 3-7 sentences of plain prose: no heading, no "Answer:" preamble, no
sentence count, no numbering. Use a short list only if the question asks for
steps.
"""


def explain_architecture(
    owner: str,
    name: str,
    graph: dict,
    question: str | None = None,
    focus_kind: str | None = None,
    focus_id: str | None = None,
) -> tuple[str, str, str | None]:
    focus_text, focus_label = _focus_block(graph, focus_kind, focus_id)
    asked = (question or "").strip() or (DEFAULT_FOCUS_QUESTION if focus_label else DEFAULT_ARCHITECTURE_QUESTION)
    prompt = _architecture_prompt(owner, name, graph, asked, focus_text)
    answer = call_with_retry(lambda: call_llm(prompt, think=ANSWER_EFFORT, max_tokens=2200)).strip()
    return asked, answer, focus_label
