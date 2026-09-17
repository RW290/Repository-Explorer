"""Proactive one-line explainers for every function and class in a file.

The line-level "ask why" (explain.py) answers a question the reader asks.
This does the opposite: the first time a file is opened in the source
viewer, every function/class definition gets a one-line explanation
without anyone asking, and the result is persisted so it costs one batched
LLM call per file, ever, shared by every later visitor.

Symbol discovery is local and language-aware where it can be: Python via
the `ast` module (the same tool the dependency parser uses), JavaScript and
TypeScript via line patterns that are deliberately conservative — a missed
function is a gap, a false one is an explanation of nothing. Other
languages get no explainers rather than guessed ones.
"""

import ast
import json
import re
from dataclasses import dataclass

from app.audience import AUDIENCE_FRAMING
from app.llm import call_llm, call_with_retry

# Source lines handed to the model per symbol: enough to see the body's
# shape, not the whole thing — a 300-line function is explained from its
# head, which is where its intent lives.
MAX_SNIPPET_LINES = 30
MAX_SNIPPET_CHARS = 1600
SYMBOLS_PER_CALL = 30
MAX_SYMBOLS_PER_FILE = 120
TEMPERATURE = 0.2

_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "with"}
_JS_PATTERNS = [
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[(<]"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (
        re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"
        ),
        "function",
    ),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b"), "function"),
    # Class methods: an indented `name(...) {` that isn't a control keyword.
    (re.compile(r"^\s+(?:(?:public|private|protected|static|async|readonly|override)\s+)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::[^{]+)?\{\s*$"), "method"),
]


@dataclass
class Symbol:
    name: str
    kind: str  # "function" | "method" | "class"
    line: int
    end_line: int


def _python_symbols(content: str) -> list[Symbol]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    symbols: list[Symbol] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                symbols.append(Symbol(name, "method" if in_class else "function", child.lineno, child.end_lineno or child.lineno))
                visit(child, f"{name}.", False)
            elif isinstance(child, ast.ClassDef):
                name = f"{prefix}{child.name}"
                symbols.append(Symbol(name, "class", child.lineno, child.end_lineno or child.lineno))
                visit(child, f"{name}.", True)

    visit(tree, "", False)
    return symbols


def _js_symbols(content: str) -> list[Symbol]:
    lines = content.splitlines()
    symbols: list[Symbol] = []
    for i, line in enumerate(lines, start=1):
        for pattern, kind in _JS_PATTERNS:
            m = pattern.match(line)
            if not m:
                continue
            name = m.group(1)
            if name in _JS_KEYWORDS:
                break
            # Approximate end: the next line at the same or lower indent that
            # starts a new top-level statement, capped for snippet purposes.
            indent = len(line) - len(line.lstrip())
            end = i
            for j in range(i, min(len(lines), i + 400)):
                text = lines[j]
                if j > i - 1 and text.strip() and (len(text) - len(text.lstrip())) <= indent and j + 1 > i:
                    end = j + 1 if text.strip().startswith("}") else j
                    break
                end = j + 1
            symbols.append(Symbol(name, kind, i, max(end, i)))
            break
    return symbols


def extract_symbols(path: str, content: str) -> list[Symbol]:
    lowered = path.lower()
    if lowered.endswith((".py", ".pyi")):
        symbols = _python_symbols(content)
    elif lowered.endswith(_JS_EXTENSIONS):
        symbols = _js_symbols(content)
    else:
        return []
    symbols.sort(key=lambda s: s.line)
    return symbols[:MAX_SYMBOLS_PER_FILE]


def _snippet(lines: list[str], symbol: Symbol) -> str:
    chunk = lines[symbol.line - 1 : min(symbol.end_line, symbol.line - 1 + MAX_SNIPPET_LINES)]
    text = "\n".join(chunk)
    if symbol.end_line - symbol.line + 1 > MAX_SNIPPET_LINES:
        text += "\n    …"
    return text[:MAX_SNIPPET_CHARS]


def _prompt(path: str, file_summary: str | None, batch: list[tuple[Symbol, str]]) -> str:
    summary_line = f'The file\'s overall role: "{file_summary}"\n\n' if file_summary else ""
    items = "\n\n".join(
        f"### {s.kind} `{s.name}` (line {s.line})\n```\n{snippet}\n```" for s, snippet in batch
    )
    return f"""For each function, method or class below from `{path}`, write ONE line
(at most 25 words) that says what it does and why it exists in this file —
the responsibility it holds, not a restatement of its signature.

{AUDIENCE_FRAMING}

{summary_line}Return ONLY a JSON array of objects with "name", "line" and "explanation"
fields, one per symbol, in the same order, no other text.

{items}
"""


def _parse(raw: str) -> list[dict]:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            return []
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def explain_symbols(path: str, content: str, file_summary: str | None = None) -> list[dict]:
    """One batched call per ~30 symbols. A symbol the model skipped or
    garbled simply gets no explainer, rather than a made-up one."""
    symbols = extract_symbols(path, content)
    if not symbols:
        return []
    lines = content.splitlines()
    results: list[dict] = []
    for i in range(0, len(symbols), SYMBOLS_PER_CALL):
        batch = [(s, _snippet(lines, s)) for s in symbols[i : i + SYMBOLS_PER_CALL]]
        prompt = _prompt(path, file_summary, batch)
        raw = call_with_retry(lambda: call_llm(prompt, temperature=TEMPERATURE, think="low", max_tokens=600 + len(batch) * 120))
        by_key: dict[tuple[str, int], str] = {}
        by_name: dict[str, str] = {}
        for item in _parse(raw):
            explanation = re.sub(r"\s+", " ", str(item.get("explanation") or "")).strip()
            if not explanation:
                continue
            name = str(item.get("name") or "").strip().strip("`")
            try:
                line = int(item.get("line"))
            except (TypeError, ValueError):
                line = -1
            by_key[(name, line)] = explanation
            by_name.setdefault(name, explanation)
        for s, _ in batch:
            explanation = by_key.get((s.name, s.line)) or by_name.get(s.name) or by_name.get(s.name.split(".")[-1])
            if not explanation:
                continue
            results.append(
                {"path": path, "name": s.name, "kind": s.kind, "line": s.line, "end_line": s.end_line, "explanation": explanation}
            )
    return results
