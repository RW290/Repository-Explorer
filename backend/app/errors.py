"""User-facing error types.

Every error surfaced to the UI should say three things: what failed, why,
and what the reader can do about it. Raw exceptions don't — a subprocess
error dumps an argv array and an exit code at someone who just typed a repo
URL. So the layers that talk to the outside world (GitHub, Ollama Cloud, git)
translate their failures into these instead, and `str(e)` is always a
sentence meant to be read by a person.

`http_status` lets the API layer answer with a meaningful code without
re-inspecting the error, and distinguishes the two audiences: 4xx means the
request was the problem (bad repo URL), 503 means the deployment is
misconfigured (missing credentials) and the operator needs to fix it.
"""


class PipelineError(Exception):
    """Base for failures with a message written for a human reader."""

    http_status = 502


class ConfigurationError(PipelineError):
    """The environment is missing something it needs. The operator must fix it."""

    http_status = 503


class GitHubAuthError(ConfigurationError):
    """No usable way to authenticate to GitHub."""


class GitHubAccessError(PipelineError):
    """GitHub answered, but the repo can't be read (missing, private, throttled)."""

    http_status = 404


class RepoCloneError(PipelineError):
    """The repository couldn't be cloned."""

    http_status = 400


class LLMConfigError(ConfigurationError):
    """The model backend is missing credentials or pointed at a bad model."""


class LLMTransientError(PipelineError):
    """A momentary model failure (per-minute rate limit, overload) worth retrying."""

    http_status = 503
