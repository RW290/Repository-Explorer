"""History miner: PR rationale extraction via the GitHub GraphQL API.

Goes through app.github_client (gh CLI locally, GITHUB_TOKEN when
deployed) rather than handling auth itself. One GraphQL call fetches a
page of merged PRs (title, body, merge commit, changed files) in one round
trip; a diff fetch is only made per-PR for the minority that need
diff-based inference (no stated rationale), keeping LLM/API round trips
batched per the latency constraint.
"""

import json
import re
import time
from dataclasses import dataclass

from app import github_client
from app.audience import AUDIENCE_FRAMING
from app.errors import GitHubAccessError
from app.llm import LLM_CONCURRENCY, call_llm, call_with_retry
from app.progress import Reporter

PR_PAGE_SIZE = 100
MAX_PRS_TO_MINE = 40
MIN_RATIONALE_CHARS = 40
TRIVIAL_PATH_SUFFIXES = (".lock", ".yml", ".yaml", ".md", ".rst", ".txt", ".toml", ".cfg", ".ini")
TRIVIAL_PATH_PREFIXES = (".github/",)
RATIONALE_BATCH_SIZE = 5
EFFORT = "low"

GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: %d, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        mergedAt
        mergeCommit { message }
        files(first: 30) { nodes { path additions deletions } }
      }
    }
  }
}
""" % PR_PAGE_SIZE


@dataclass
class RawPR:
    number: int
    title: str
    body: str
    merged_at: str
    merge_commit_message: str
    files: list[str]


def fetch_merged_prs(owner: str, name: str, limit: int = MAX_PRS_TO_MINE) -> list[RawPR]:
    prs: list[RawPR] = []
    cursor = None
    while len(prs) < limit:
        data = github_client.graphql(GRAPHQL_QUERY, {"owner": owner, "name": name, "cursor": cursor})
        page = data["data"]["repository"]["pullRequests"]
        for node in page["nodes"]:
            prs.append(
                RawPR(
                    number=node["number"],
                    title=node["title"],
                    body=node["body"] or "",
                    merged_at=node["mergedAt"],
                    merge_commit_message=(node.get("mergeCommit") or {}).get("message") or "",
                    files=[f["path"] for f in node["files"]["nodes"]],
                )
            )
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return prs[:limit]


def is_trivial(pr: RawPR, known_files: set[str]) -> bool:
    if not pr.files:
        return True
    relevant_files = [
        f
        for f in pr.files
        if f in known_files
        and not f.endswith(TRIVIAL_PATH_SUFFIXES)
        and not any(f.startswith(p) for p in TRIVIAL_PATH_PREFIXES)
    ]
    return len(relevant_files) == 0


def _candidate_stated_rationale(pr: RawPR) -> str | None:
    body = pr.body.strip()
    if len(body) >= MIN_RATIONALE_CHARS:
        return body
    # Squash-merged repos can carry the real description in the merge commit body
    # instead of the PR body — check there before assuming the signal is lost.
    commit_msg = pr.merge_commit_message.strip()
    commit_body = "\n".join(commit_msg.splitlines()[1:]).strip()
    if len(commit_body) >= MIN_RATIONALE_CHARS:
        return commit_body
    return None


def _extraction_prompt(batch: list[dict]) -> str:
    items_text = "\n\n".join(
        f"### PR #{item['number']}: {item['title']}\n"
        f"Changed files: {', '.join(item['files'][:10])}\n"
        + (f"Stated description:\n{item['stated']}\n" if item["stated"] else f"Diff (no description given):\n{item['diff']}\n")
        for item in batch
    )
    return f"""For each pull request below, extract why the change was made.

{AUDIENCE_FRAMING}

State the engineering reason precisely — bug class, performance/latency
issue, API contract change, security fix, refactor motivation, and so on —
rather than softening it into a vague summary. Keep identifiers that matter
(CVE numbers, function/library names) exactly as given; don't paraphrase
them away. Do not change or soften the actual facts — only change how
precisely they're explained.

Return ONLY a JSON array of objects, one per PR, each with these exact fields:
- "number": the PR number (integer)
- "rationale_stated": if a "Stated description" is given, the author's own
  reason restated in 1-2 sentences of your own words. Faithful to what they
  meant, but never copied: no sentences lifted from the description, no
  first person ("I noticed…"), no links, no checklists. Say what the
  engineering reason was. If no description is given, null.
- "rationale_inferred": only fill this in if there was no stated description —
  your best 1-2 sentence technical guess at the reason, based solely on
  the diff. Otherwise null.
- "confidence": "high" if rationale_stated is filled from a clear description,
  "medium" if rationale_inferred from an unambiguous diff pattern, "low" if
  rationale_inferred from an ambiguous diff.
- "diff_summary": one precise sentence describing what changed, regardless
  of why.

Never fill in both rationale_stated and rationale_inferred for the same PR.

Pull requests:
{items_text}
"""


def parse_extractions(raw: str) -> list[dict]:
    """The model's JSON array of extractions, tolerantly. Replies arrive
    wrapped in fences or prose, or cut off mid-array; every complete object is
    kept, so one malformed reply costs the broken entries, not the batch."""
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    for candidate in (cleaned, cleaned[cleaned.find("[") : cleaned.rfind("]") + 1] if "[" in cleaned else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    salvaged = []
    # Extraction objects are flat (no nested braces), so each complete one
    # can be lifted out on its own.
    for match in re.finditer(r"\{[^{}]*\}", cleaned):
        try:
            item = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "number" in item:
            salvaged.append(item)
    return salvaged


def extract_rationales(owner: str, name: str, prs: list[RawPR], reporter: Reporter | None = None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    items = []
    for pr in prs:
        stated = _candidate_stated_rationale(pr)
        diff = "" if stated else github_client.pr_diff(owner, name, pr.number)[:4000]
        items.append({"number": pr.number, "title": pr.title, "files": pr.files, "stated": stated, "diff": diff})

    batches = [items[i : i + RATIONALE_BATCH_SIZE] for i in range(0, len(items), RATIONALE_BATCH_SIZE)]

    def run(batch: list[dict]) -> list[dict]:
        prompt = _extraction_prompt(batch)
        # Low reasoning effort, like the other prompts — decided by the eval
        # harness (`python -m evals run prs --variant default --variant
        # low:think=low`), not by reading one output. Both efforts score the
        # same on every check (coverage, stated/inferred kind, confidence,
        # verbatim overlap with the author) and read the same side by side;
        # low is ~25% faster, and this is the longest stage of an analysis.
        # What actually stops the model quoting the author instead of stating
        # the reason is the prompt's "restated… never copied" rule, which took
        # mean verbatim overlap from 0.77 to under 0.03 at either effort.
        raw = call_with_retry(lambda: call_llm(prompt, think=EFFORT, max_tokens=1200 + len(batch) * 500))
        results = parse_extractions(raw)
        returned = {r.get("number") for r in results}
        # Whatever the reply didn't cover still gets an entry, marked as such.
        results.extend(
            {
                "number": item["number"],
                "rationale_stated": item["stated"],
                "rationale_inferred": None,
                "confidence": "low",
                "diff_summary": "Diff summary unavailable (LLM response could not be parsed).",
            }
            for item in batch
            if item["number"] not in returned
        )
        return results

    extractions: list[dict] = []
    done = 0
    if batches:
        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as pool:
            futures = {pool.submit(run, batch): batch for batch in batches}
            for future in as_completed(futures):
                extractions.extend(future.result())
                done += len(futures[future])
                if reporter:
                    reporter.advance("history", done)
                    reporter.event("read PRs " + ", ".join(f"#{item['number']}" for item in futures[future]), kind="history")

    by_number = {pr.number: pr for pr in prs}
    merged = []
    for ext in extractions:
        pr = by_number.get(ext.get("number"))
        if pr is None:
            continue
        merged.append(
            {
                "pr": pr,
                "rationale_stated": ext.get("rationale_stated"),
                "rationale_inferred": ext.get("rationale_inferred"),
                "confidence": ext.get("confidence", "low"),
                "diff_summary": ext.get("diff_summary", ""),
            }
        )
    return merged


_TRANSIENT_NETWORK_MARKERS = ("connection reset", "timed out", "timeout", "eof", "temporarily", "502", "503", "504")


def _fetch_with_retry(owner: str, name: str, attempts: int = 3) -> list[RawPR]:
    """A dropped connection to GitHub minutes into an analysis shouldn't throw
    the whole thing away. Only network-shaped failures are retried: a missing
    token or a private repo won't fix itself and should surface at once."""
    for attempt in range(attempts):
        try:
            return fetch_merged_prs(owner, name)
        except GitHubAccessError as e:
            transient = any(marker in str(e).lower() for marker in _TRANSIENT_NETWORK_MARKERS)
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def run_miner(owner: str, name: str, known_files: set[str], reporter: Reporter | None = None) -> list[dict]:
    if reporter:
        reporter.start("history", detail="fetching merged pull requests")
    prs = _fetch_with_retry(owner, name)
    non_trivial = [pr for pr in prs if not is_trivial(pr, known_files)]
    if reporter:
        reporter.start("history", total=len(non_trivial) or None, detail=None if non_trivial else "no substantive PRs")
        reporter.event(f"{len(prs)} merged PRs fetched, {len(non_trivial)} worth reading")
    extractions = extract_rationales(owner, name, non_trivial, reporter=reporter)
    if reporter:
        reporter.finish("history", f"{len(extractions)} of {len(prs)} PRs explained")
    return extractions
