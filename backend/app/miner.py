"""History miner (phase 3): PR extraction via the GitHub GraphQL API.

Shells out to the `gh` CLI rather than handling a raw PAT ourselves — it
already holds an authenticated session, so there's no token to store or
leak. One GraphQL call fetches a page of merged PRs (title, body, merge
commit, changed files) in one round trip; `gh pr diff` is only called
per-PR for the minority that need diff-based inference (no stated
rationale), keeping LLM/API round trips batched per the latency constraint.
"""

import json
import subprocess
import time
from dataclasses import dataclass

from app.llm import call_llm, call_with_retry

PR_PAGE_SIZE = 100
MAX_PRS_TO_MINE = 40
MIN_RATIONALE_CHARS = 40
TRIVIAL_PATH_SUFFIXES = (".lock", ".yml", ".yaml", ".md", ".rst", ".txt", ".toml", ".cfg", ".ini")
TRIVIAL_PATH_PREFIXES = (".github/",)
RATIONALE_BATCH_SIZE = 5

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


def _gh_graphql(owner: str, name: str, cursor: str | None) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={GRAPHQL_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
    if cursor:
        args += ["-f", f"cursor={cursor}"]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def fetch_merged_prs(owner: str, name: str, limit: int = MAX_PRS_TO_MINE) -> list[RawPR]:
    prs: list[RawPR] = []
    cursor = None
    while len(prs) < limit:
        data = _gh_graphql(owner, name, cursor)
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


def _fetch_diff(owner: str, name: str, number: int) -> str:
    result = subprocess.run(
        ["gh", "pr", "diff", str(number), "-R", f"{owner}/{name}"],
        capture_output=True,
        text=True,
    )
    return result.stdout[:4000] if result.returncode == 0 else ""


def _extraction_prompt(batch: list[dict]) -> str:
    items_text = "\n\n".join(
        f"### PR #{item['number']}: {item['title']}\n"
        f"Changed files: {', '.join(item['files'][:10])}\n"
        + (f"Stated description:\n{item['stated']}\n" if item["stated"] else f"Diff (no description given):\n{item['diff']}\n")
        for item in batch
    )
    return f"""For each pull request below, extract why the change was made.

Return ONLY a JSON array of objects, one per PR, each with these exact fields:
- "number": the PR number (integer)
- "rationale_stated": if a "Stated description" is given, a concise 1-2 sentence
  paraphrase of the author's own stated reason. If none is given, null.
- "rationale_inferred": only fill this in if there was no stated description —
  your best 1-2 sentence guess at the reason, based solely on the diff. Otherwise null.
- "confidence": "high" if rationale_stated is filled from a clear description,
  "medium" if rationale_inferred from an unambiguous diff pattern, "low" if
  rationale_inferred from an ambiguous diff.
- "diff_summary": one sentence describing what changed, regardless of why.

Never fill in both rationale_stated and rationale_inferred for the same PR.

Pull requests:
{items_text}
"""


def extract_rationales(owner: str, name: str, prs: list[RawPR]) -> list[dict]:
    items = []
    for pr in prs:
        stated = _candidate_stated_rationale(pr)
        diff = "" if stated else _fetch_diff(owner, name, pr.number)
        items.append({"number": pr.number, "title": pr.title, "files": pr.files, "stated": stated, "diff": diff})

    extractions: list[dict] = []
    for i in range(0, len(items), RATIONALE_BATCH_SIZE):
        if i > 0:
            time.sleep(2)
        batch = items[i : i + RATIONALE_BATCH_SIZE]
        prompt = _extraction_prompt(batch)
        raw = call_with_retry(lambda: call_llm(prompt))
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            results = json.loads(cleaned)
            extractions.extend(results)
        except json.JSONDecodeError:
            for item in batch:
                extractions.append(
                    {
                        "number": item["number"],
                        "rationale_stated": item["stated"],
                        "rationale_inferred": None,
                        "confidence": "low",
                        "diff_summary": "Diff summary unavailable (LLM response could not be parsed).",
                    }
                )

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


def run_miner(owner: str, name: str, known_files: set[str]) -> list[dict]:
    prs = fetch_merged_prs(owner, name)
    non_trivial = [pr for pr in prs if not is_trivial(pr, known_files)]
    return extract_rationales(owner, name, non_trivial)
