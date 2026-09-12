"""GitHub API access, in two modes.

Locally, we shell out to the `gh` CLI's already-authenticated session — no
raw token to store or leak on a dev machine. A deployed instance has no such
session, so it needs a real credential: set GITHUB_TOKEN (a personal access
token with public repo read scope is enough) and this switches to calling
the API directly over HTTPS instead. Same interface either way, so callers
never need to know which mode is active.

Both paths translate their failures into app.errors types. The raw versions
are actively misleading to a reader — a `gh` auth failure surfaces as
"non-zero exit status 4" with the entire GraphQL query pasted into the
message, which says nothing about the actual problem (no credentials in
this environment).
"""

import json
import os
import subprocess

import httpx

from app.errors import GitHubAccessError, GitHubAuthError

GITHUB_API = "https://api.github.com"

# `gh` uses this exit code for "not logged in / bad credentials".
_GH_AUTH_EXIT_CODE = 4

_NO_CREDENTIALS_HELP = (
    "This server has no way to authenticate to GitHub. Set a GITHUB_TOKEN "
    "environment variable (a GitHub personal access token with read access to "
    "public repositories is enough) and restart. On a hosted deployment that's "
    "the only option — the `gh` command-line fallback only works on a machine "
    "where someone has run `gh auth login`."
)


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN")


def _repo_label(variables: dict) -> str:
    owner, name = variables.get("owner"), variables.get("name")
    return f"{owner}/{name}" if owner and name else "that repository"


def _raise_for_token_response(resp: httpx.Response, target: str) -> None:
    if resp.status_code == 401:
        raise GitHubAuthError(
            "GitHub rejected this server's GITHUB_TOKEN as invalid or expired. "
            "Generate a new personal access token and update the GITHUB_TOKEN secret."
        )
    if resp.status_code == 403:
        if resp.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubAccessError(
                "This server has hit GitHub's hourly API rate limit. Wait for the "
                "limit to reset (usually within an hour) and try again."
            )
        raise GitHubAccessError(
            f"GitHub refused access to {target}. If it's a private repository, this "
            "server's GITHUB_TOKEN doesn't have permission to read it."
        )
    if resp.status_code == 404:
        raise GitHubAccessError(
            f"GitHub has no repository at {target}. Check the spelling — and note that "
            "private repositories are only visible if this server's token can read them."
        )
    if resp.status_code >= 400:
        raise GitHubAccessError(
            f"GitHub returned an unexpected error ({resp.status_code}) for {target}."
        )


def _raise_for_graphql_errors(payload: dict, target: str) -> None:
    errors = payload.get("errors")
    if not errors:
        return
    kinds = {e.get("type") for e in errors}
    if "NOT_FOUND" in kinds:
        raise GitHubAccessError(
            f"GitHub has no repository at {target}. Check the spelling — and note that "
            "private repositories are only visible if this server's token can read them."
        )
    if "RATE_LIMITED" in kinds:
        raise GitHubAccessError(
            "This server has hit GitHub's API rate limit. Wait for it to reset and try again."
        )
    first = next((e.get("message") for e in errors if e.get("message")), "no details given")
    raise GitHubAccessError(f"GitHub rejected the request for {target}: {first}")


def _raise_for_gh_stderr(stderr: str, returncode: int, target: str) -> None:
    """`gh` exits non-zero on GraphQL-level errors too, so the same conditions
    _raise_for_graphql_errors handles on the token path arrive here as text."""
    lowered = stderr.lower()
    if "could not resolve to a repository" in lowered or "not found" in lowered:
        raise GitHubAccessError(
            f"GitHub has no repository at {target}. Check the spelling — and note that "
            "private repositories are only visible if this server's token can read them."
        )
    if "rate limit" in lowered:
        raise GitHubAccessError(
            "This server has hit GitHub's API rate limit. Wait for it to reset and try again."
        )
    if "bad credentials" in lowered or "authentication" in lowered:
        raise GitHubAuthError("GitHub rejected this server's credentials. " + _NO_CREDENTIALS_HELP)
    detail = stderr.splitlines()[0].removeprefix("gh: ") if stderr else f"exit code {returncode}"
    raise GitHubAccessError(f"Reading {target} from GitHub failed: {detail}")


def _run_gh(args: list[str], target: str) -> str:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise GitHubAuthError(
            "The `gh` command-line tool isn't installed in this environment. " + _NO_CREDENTIALS_HELP
        ) from None
    except subprocess.CalledProcessError as e:
        if e.returncode == _GH_AUTH_EXIT_CODE:
            raise GitHubAuthError(
                "The `gh` command-line tool is installed but not signed in to GitHub. "
                + _NO_CREDENTIALS_HELP
            ) from None
        _raise_for_gh_stderr((e.stderr or "").strip(), e.returncode, target)
    return result.stdout


def graphql(query: str, variables: dict) -> dict:
    target = _repo_label(variables)
    token = _token()

    if token:
        try:
            resp = httpx.post(
                f"{GITHUB_API}/graphql",
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except httpx.RequestError as e:
            raise GitHubAccessError(
                f"Couldn't reach GitHub to read {target} ({e.__class__.__name__}). "
                "Check this server's network access and try again."
            ) from None
        _raise_for_token_response(resp, target)
        payload = resp.json()
        _raise_for_graphql_errors(payload, target)
        return payload

    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        args += ["-f", f"{key}={value}"]
    payload = json.loads(_run_gh(args, target))
    _raise_for_graphql_errors(payload, target)
    return payload


def pr_diff(owner: str, name: str, number: int) -> str:
    """Best-effort: a missing diff degrades an annotation's confidence rather
    than failing the run, so this returns "" instead of raising."""
    token = _token()
    if token:
        try:
            resp = httpx.get(
                f"{GITHUB_API}/repos/{owner}/{name}/pulls/{number}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
                timeout=30,
            )
        except httpx.RequestError:
            return ""
        return resp.text if resp.status_code == 200 else ""

    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(number), "-R", f"{owner}/{name}"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ""
    return result.stdout if result.returncode == 0 else ""
