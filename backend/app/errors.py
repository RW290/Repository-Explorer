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
    http_status = 502


class ConfigurationError(PipelineError):
    http_status = 503


class GitHubAuthError(ConfigurationError):
    pass


class GitHubAccessError(PipelineError):
    http_status = 404


class RepoCloneError(PipelineError):
    http_status = 400


class LLMConfigError(ConfigurationError):
    pass


class LLMTransientError(PipelineError):
    http_status = 503
