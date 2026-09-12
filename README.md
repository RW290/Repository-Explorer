# repo-explorer

Ingests a GitHub repo and produces an explorable, animated visualization of
its architecture, annotated with *why* things are the way they are — pulled
from PR descriptions and linked issues. See
[`repo-breakdown-build-brief.md`](repo-breakdown-build-brief.md) for the full
design.

Priority order: accuracy of the "why" annotations > the animated viewer >
breadth of repos it works on.

## Status

**All 4 phases done, validated against two structurally different repos:**
[psf/requests](https://github.com/psf/requests) (mature, `src/`-layout,
heavy PR discipline) and
[CommanderBlop/scribe-dictation](https://github.com/CommanderBlop/scribe-dictation)
(small solo project, flat layout, almost no PR history).

- **Parser** (`backend/app/parser.py`) — shallow-clones the repo, builds a
  file-level import graph via Python's `ast` module (no LLM), then batches
  files into Gemini Flash calls for summaries. File discovery works for any
  layout (not just `src/`), and import resolution handles both absolute and
  relative imports.
- **Miner** (`backend/app/miner.py`) — fetches merged PRs via the GitHub
  GraphQL API through `app/github_client.py` (the `gh` CLI locally, a real
  token when deployed — see Secrets below), filters trivial/irrelevant
  diffs locally, and batches the rest into Gemini Flash calls for rationale
  extraction. Falls back to the merge commit body for squash-merged PRs,
  and only fetches a full diff for the minority of PRs with no usable
  description.
- **Merge** (`backend/app/merge.py`) — attaches annotations to nodes.
- **Viewer** — same animated pan/zoom UI from phase 1, now pointed at real
  data. The landing screen lets you type a repo URL; results are cached to
  `backend/.cache/` (gitignored) since a fresh run costs real LLM/API calls.

All LLM-written text (file summaries and PR rationale) is prompted to read
as plain language for a non-technical reader — no unexplained jargon, terms
briefly explained inline when unavoidable — without softening or changing
the underlying facts.

Spot-checked a sample of annotations from both repos by hand against the
actual PRs (e.g. `requests`' CVE-2024-47081 fix and v2.32.5 SSLContext
revert; `scribe-dictation`'s session-mode and pacing-timer PRs) — rationale
text matched the real PR content, and thin/missing PR descriptions
correctly fell back to `rationale_inferred` with lower confidence rather
than being presented as stated fact.

The parser's import resolution (absolute + relative imports, `src/`-layout
awareness with a generic fallback) and PR mining have now been exercised
against two differently-shaped repos, but still only Python ones — a
heavily dynamic-import codebase or a non-Python repo hasn't been tried.

## Layout

- `backend/` — FastAPI server (Python).
  - `GET /health` — plain liveness check for hosting platforms.
  - `GET /api/graph` — the phase-1 fixture, zero cost/latency.
  - `GET /api/graph?repo_url=...` — synchronous real-pipeline path. Fine for
    local dev; a hosted deployment should avoid it, since an uncached repo
    takes minutes and most platforms time out a request well before that.
  - `POST /api/analyze {repo_url, force_refresh?}` — the deployment-safe
    path. Returns immediately: `{status: "done", graph}` if already cached,
    otherwise `{job_id, status: "running"}` and starts the pipeline in a
    background thread (see `backend/app/jobs.py`).
  - `GET /api/analyze/{job_id}` — poll a job's `status` ("running" / "done"
    / "error"), current `stage` (e.g. "summarizing files (batch 2/6)"), and
    once done, the resulting `graph`.
- `frontend/` — React + TypeScript + Vite viewer with an animated pan/zoom
  camera: repo level shows folders, clicking a folder zooms into its files,
  clicking a file zooms into full detail (summary, dependencies,
  annotations with stated-vs-inferred rationale visually distinguished).
  The landing page uses the async job endpoints and polls every 3s,
  displaying the live stage text while a fresh analysis runs.

## Running locally

Backend:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite dev server URL (typically `http://localhost:5173`). The
landing page defaults to `https://github.com/psf/requests` (already
cached); type a different repo URL to run the pipeline fresh, or use the
"load the phase-1 fixture demo instead" link to skip the network entirely.

## Secrets

`GEMINI_API_KEY` lives in `.env` at the repo root (gitignored, never
committed). Get/rotate it at [aistudio.google.com](https://aistudio.google.com).
`llm.py` refuses to call any model whose name doesn't contain "flash" —
that's the one thing that would turn this from free to billed. The default
model is pinned to a specific version (`gemini-3.6-flash` as of writing),
not a `-latest` alias — an alias silently resolved to a preview model with
a 20-requests-*per day* free quota (much stricter than the usual per-minute
limit) partway through building this. Check aistudio.google.com for the
current recommended Flash version before assuming this pin is still right.

GitHub API access goes through `app/github_client.py`: locally it shells
out to the `gh` CLI's own stored auth (`gh auth login`), so nothing
GitHub-related needs a secret on your dev machine. A deployed instance has
no such session, so set `GITHUB_TOKEN` (a personal access token with public
repo read access) and it switches to calling the API directly instead —
see `.env.example`.

## Deployment

Not deployed anywhere yet — this section is what's been done to make that
possible, and what's still your call to make before picking a host.

**Done:**
- Analysis runs as an async job (`POST /api/analyze` + polling), so it
  survives a normal request timeout. Confirmed by running a full job
  through the poll loop end-to-end (see `backend/app/jobs.py`).
- GitHub auth works via a portable `GITHUB_TOKEN` env var, not just the
  local `gh` CLI session — verified the token code path actually makes an
  authenticated HTTPS call (tested against the real API with a deliberately
  invalid token, confirming it hits GitHub and fails cleanly rather than
  silently falling through).
- CORS origins are configurable (`CORS_ORIGINS` env var) instead of
  hardcoded to localhost.
- The frontend's backend URL is configurable at build time (`VITE_API_BASE`
  in `frontend/.env`) instead of hardcoded to localhost.
- Gemini quota exhaustion now surfaces a plain-language error to the user
  instead of a raw stack trace (see `_friendly_error` in `jobs.py`).
- Added `GET /health` for platform health checks.

**Still your call, not done for you:**
- **Which host.** Backend needs Python + outbound HTTPS + a process that
  stays warm long enough for background jobs (a few minutes) — a
  serverless/function platform with short execution limits won't work for
  the job thread, though the polling requests themselves are quick.
  Frontend is a static Vite build, deployable almost anywhere.
- **The 20-requests/day Gemini quota** is real and shared across everyone
  who uses a deployed instance — one person analyzing one new repo can burn
  it for the rest of the day. Worth deciding whether that's acceptable for
  a public demo, whether to add your own rate-limiting on top, or whether a
  paid Gemini tier makes sense before real traffic arrives.
- **Job state is in-memory** (`jobs.py`), not persisted — fine for a single
  instance, but a restart loses in-progress job status (the finished graph
  is still safe in `backend/.cache/` once a job completes). Don't run
  multiple backend instances behind a load balancer without changing this.
- **No auth/abuse protection** on `/api/analyze` — anyone can point it at
  any public GitHub repo. Fine for a personal/demo deployment behind an
  unguessable URL; not fine as a fully public, indexed service.

## Known limitations / not yet done

- Tested against two Python repos only. No multi-language support, by
  design (v1 scope, Python only) — untried on non-Python or heavily
  dynamic-import codebases.
- The viewer's zoom model is 2 levels deep (folder → file); a repo with
  deeply nested subpackages gets flattened one level.
- Annotation mining is capped at the 40 most recently updated merged PRs
  per repo, filtered down to non-trivial ones — a deliberate cost/latency
  tradeoff, not full history. A repo with very little PR history (like
  `scribe-dictation`) will surface only a handful of annotations, which is
  correct behavior (the brief calls for degrading to mostly-null rather
  than fabricating), not a bug.
- Free-tier Gemini quota is the practical bottleneck: roughly one
  medium-sized repo's worth of fresh analysis per day on a single API key.
  Cached repos are unaffected.
- No "generate a build-your-own playbook" feature — out of scope for v1 per
  the build brief.
