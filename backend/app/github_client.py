"""GitHub API access, in two modes.

Locally, we shell out to the `gh` CLI's already-authenticated session — no
raw token to store or leak on a dev machine. A deployed instance has no such
session, so it needs a real credential: set GITHUB_TOKEN (a personal access
token with public_repo read scope is enough) and this switches to calling
the API directly over HTTPS instead. Same interface either way, so callers
never need to know which mode is active.
"""

import json
import os
import subprocess

import httpx

GITHUB_API = "https://api.github.com"


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN")


def graphql(query: str, variables: dict) -> dict:
    token = _token()
    if token:
        resp = httpx.post(
            f"{GITHUB_API}/graphql",
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        args += ["-f", f"{key}={value}"]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def pr_diff(owner: str, name: str, number: int) -> str:
    token = _token()
    if token:
        resp = httpx.get(
            f"{GITHUB_API}/repos/{owner}/{name}/pulls/{number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
            timeout=30,
        )
        return resp.text if resp.status_code == 200 else ""

    result = subprocess.run(
        ["gh", "pr", "diff", str(number), "-R", f"{owner}/{name}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""
