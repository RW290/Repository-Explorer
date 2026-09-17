# Repository-Explorer

Ingests a GitHub repo and produces an explorable, animated visualization of
its architecture, annotated with *why* things are the way they are — pulled
from PR descriptions and linked issues. See
[`repo-breakdown-build-brief.md`](repo-breakdown-build-brief.md) for the full
design.

**Live**: [repository-explorer.replit.app](https://repository-explorer.replit.app)

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
  every file (not just `.py` — see Known limitations) into LLM calls for
  summaries. File discovery works for any layout (not just `src/`), and
  import resolution handles both absolute and relative imports. Also
  generates a one-time project overview (from the repo's README, or folder
  structure if there's no README) shown at the top zoom level in the viewer.
- **Miner** (`backend/app/miner.py`) — fetches merged PRs via the GitHub
  GraphQL API through `app/github_client.py` (the `gh` CLI locally, a real
  token when deployed — see Secrets below), filters trivial/irrelevant
  diffs locally, and batches the rest into LLM calls for rationale
  extraction. Falls back to the merge commit body for squash-merged PRs,
  and only fetches a full diff for the minority of PRs with no usable
  description.
- **Merge** (`backend/app/merge.py`) — attaches annotations to nodes.
- **Architecture map** (`backend/app/architecture.py`) — a semantic view
  laid over the file graph, in the spirit of
  [gitdiagram](https://github.com/ahmedkhaleel2004/gitdiagram). The map's
  boxes are the graph's own file and folder nodes: the model doesn't invent
  components, it picks the 8–20 files/folders a reader needs, sorts them
  into 2–6 groups by architectural role (frontend / HTTP API / core / LLM /
  tests …), and names the main flows between them. It gets the per-file
  summaries and the real import edges (not just a file tree) and returns a
  bounded JSON AST, never Mermaid — the frontend compiles that
  deterministically. Members that don't resolve to a real node are dropped,
  not guessed, and every flow is checked against the import graph: flows
  the imports back are drawn solid, flows the model asserted but imports
  can't see (HTTP calls, subprocesses, external APIs) are kept but drawn
  dashed — the same stated-vs-inferred rule the PR annotations follow.
  Systems outside the repo (a library it builds on, an HTTP API, an LLM
  service) can appear as greyed "external" hexagons, the one kind of box
  that isn't a file. Runs as the last pipeline stage; repos cached before
  it existed get one built lazily on first open.
- **Function explainers** (`backend/app/symbols.py`) — the first time a
  file's source is opened, every function, method and class in it gets a
  proactive one-line explanation (what it does and why it exists), shown
  inline above its definition and listed in the side panel. Symbols are
  found locally — Python via `ast`, JavaScript/TypeScript via conservative
  line patterns, other languages get none rather than guesses — and
  explained in one batched LLM call per ~30 symbols, then persisted per
  file (`POST /api/repos/{owner}/{name}/symbol-explainers`) so it costs
  one call per file, ever.
- **Explain** (`backend/app/explain.py`) — three interactive, on-demand
  "ask why" variants, each persisted server-side and shared with every
  future visitor to that repo rather than kept per-browser: a highlighted
  line range, a whole file (grounded in what it depends on and what
  depends on it), or a follow-up question about the project overview.
- **Viewer** — same animated pan/zoom UI from phase 1, now pointed at real
  data. The landing screen lets you type a repo URL; results are cached to
  `backend/.cache/` (gitignored) since a fresh run costs real LLM/API calls.

All LLM-written text (file summaries, PR rationale, project overview, and
all three "ask why" variants) shares one framing (`app/audience.py`): the
reader is a software engineer using this to learn good coding practice and
system design, so answers should ground the *design rationale* behind a
choice — tradeoffs, patterns, and QoS implications like latency/scalability/
coupling where genuinely relevant — not just describe what code does, and
not soften or change the underlying facts to be more approachable. File
summaries and the "why does this file exist?" answers are additionally
grounded in the file's actual dependency edges (what it imports, what
imports it), not just its own content in isolation.

Spot-checked a sample of annotations from both repos by hand against the
actual PRs (e.g. `requests`' CVE-2024-47081 fix and v2.32.5 SSLContext
revert; `scribe-dictation`'s session-mode and pacing-timer PRs) — rationale
text matched the real PR content, and thin/missing PR descriptions
correctly fell back to `rationale_inferred` with lower confidence rather
than being presented as stated fact.

The parser's import resolution (absolute + relative imports, `src/`-layout
awareness with a generic fallback) and PR mining have now been exercised
against two differently-shaped Python repos. File *discovery* and
summarization work for any language now (see Known limitations), but a
heavily dynamic-import codebase, or a repo where most of the interesting
structure lives in a non-Python dependency graph, hasn't been tried.

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
  - `POST /api/repos/{owner}/{name}/architecture {force_refresh?}` — build
    (or return the cached) architecture map for an already-analyzed repo.
    New analyses carry it in the graph; this backfills older caches with one
    LLM call instead of a full re-analysis.
  - `GET /api/repos/{owner}/{name}/file?path=...` — raw source for a file,
    fetched from GitHub on demand (not stored during analysis).
  - `GET`/`POST /api/repos/{owner}/{name}/rationales` — read or ask a
    "why is this used?" question about a highlighted line range.
  - `GET`/`POST /api/repos/{owner}/{name}/file-rationales` — read or ask
    "why does this file exist?", grounded in its dependencies/dependents.
  - `GET`/`POST /api/repos/{owner}/{name}/project-rationales` — read or ask
    a follow-up question about the project overview.
  - All three rationale POST endpoints persist their answer to
    `backend/.cache/` (see `backend/app/rationale_store.py`), shared with
    every future visitor to that repo rather than kept per-browser.
- `frontend/` — React + TypeScript + Vite viewer. The repo level has two
  modes: **map** (default when available) renders the architecture map as
  a Mermaid flowchart laid out by ELK on a freely pannable/zoomable canvas
  (drag to pan, wheel or pinch to zoom, fit/zoom controls; the compiler is
  `src/mermaid.ts`, the view `src/ArchitectureView.tsx`). The boxes are the
  same folder/file nodes as the grid, in the same folder/Python/other
  colours, each with its folder path and the first line of its summary,
  grouped into the map's semantic layers. Clicking one opens the
  usual detail panel (summary, dependencies, history, source, "why does
  this file exist?") plus which group it sits in and its in/out flows on
  the map, each tagged import-verified or inferred; "Open in explorer"
  drops into the second mode at that node. An "all imports" toggle overlays
  every other real import between the map's nodes, thin and unlabeled, so
  the model's chosen flows can be checked against the whole truth.
  **folders** is the animated camera from before: folders (plus the
  project overview, if one was generated), clicking a folder zooms into its
  files, clicking a file zooms into full detail (summary, dependencies,
  annotations with stated-vs-inferred rationale visually distinguished, a
  source viewer with line-level "ask why," and a file-level "why does this
  file exist?"). Mermaid runs in strict security mode with SVG labels and
  no click directives; node clicks are wired on the rendered SVG. It's
  lazy-loaded with the map so the landing bundle is unchanged.
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

`OLLAMA_API_KEY` lives in `.env` at the repo root (gitignored, never
committed). Create/rotate it at
[ollama.com/settings/keys](https://ollama.com/settings/keys) — the free
tier needs no card. File summaries and PR rationale run against an
open-weight model (`gpt-oss:20b` by default) served through Ollama Cloud,
Ollama's own hosted GPU service — not a model run on this server or in the
browser. Free-tier usage is quota'd by GPU-time and resets every few hours
plus a weekly cap, not a flat monthly credit. Override the model via the
`OLLAMA_MODEL` env var — see the comment above `DEFAULT_MODEL` in
`backend/app/llm.py` for the current free-tier model list before changing.

GitHub API access goes through `app/github_client.py`: locally it shells
out to the `gh` CLI's own stored auth (`gh auth login`), so nothing
GitHub-related needs a secret on your dev machine. A deployed instance has
no such session, so set `GITHUB_TOKEN` (a personal access token with public
repo read access) and it switches to calling the API directly instead —
see `.env.example`.

## Deployment

Deployed at [repository-explorer.replit.app](https://repository-explorer.replit.app).
This section covers what makes that work, and what's still worth knowing
as traffic grows.

**Done, to make hosting viable at all:**
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
- Added `GET /health` for platform health checks.
- Every failure that can reach a user is translated into a sentence that
  says what broke, why, and what to do about it (`backend/app/errors.py`).
  The layers that touch the outside world — GitHub, Ollama Cloud, `git
  clone` — each map their own failure modes, so nobody sees an argv dump or
  an exit code. Missing credentials, an invalid or expired key, a private
  or misspelled repo, a retired model, and an exhausted free-tier quota all
  have their own message. Anything unrecognized is labeled plainly as a bug
  in this tool rather than dressed up as user error.
- Rate-limit exhaustion is distinguished from a hard quota block. Both can
  arrive as similar-looking errors, but only the first is worth retrying —
  a naive retry-everything policy would otherwise burn five backoff
  attempts (~15s) on a failure that won't resolve within that window.

**Hosting shape:** a single Replit **Autoscale** service — one FastAPI
process serves both the API and the built frontend, scaling to zero when
idle. That's a deliberate choice over Reserved VM (which runs 24/7 at a
flat cost): this app's traffic is sporadic, not sustained, so paying only
for actual usage — plus a Replit spending limit as a hard ceiling — beats
paying a flat rate to stay warm with nobody visiting. The cost of that
choice is a real one: the first request after an idle period pays a
few-second cold-start latency while a fresh instance boots, visible as a
transient failed health check in the deploy logs (not a bug — retry and
it resolves once the instance is warm).

**Still worth knowing, if traffic grows:**
- **The Ollama Cloud free-tier quota** is GPU-time-based, shared across
  everyone who uses this deployed instance, and resets every few hours plus
  a weekly cap — much more forgiving than the Gemini free tier this
  started on (20 requests/*day*), but still a real ceiling under sustained
  concurrent use. `HF_TOKEN`/Hugging Face was an intermediate backend,
  since replaced.
- **Job state is in-memory** (`jobs.py`), not persisted — fine for a single
  instance, but a restart loses in-progress job status (the finished graph
  is still safe in `backend/.cache/` once a job completes). Don't run
  multiple backend instances behind a load balancer without changing this.
- **No auth/abuse protection** on `/api/analyze` — anyone can point it at
  any public GitHub repo. Fine for a personal/demo deployment behind an
  unguessable URL; not fine as a fully public, indexed service.

## Known limitations / not yet done

- Every file in a repo becomes a node with an LLM summary, regardless of
  language (lockfiles, generated/vendor output, and binary/media files are
  excluded — see `EXCLUDE_FILE_NAMES`/`EXCLUDE_FILE_SUFFIXES` in
  `backend/app/parser.py`). Dependency *edges*, though, are Python-only —
  there's no import-graph resolution for other languages, so a JS/Rust/etc.
  file always shows as a node with no arrows in or out.
- The viewer's zoom model is 2 levels deep (folder → file); a repo with
  deeply nested subpackages gets flattened one level.
- The architecture map's selection and grouping are the model's judgment:
  which files matter and which layer each belongs to is something the
  validator can only bound (real nodes, real import edges), not verify.
  External boxes are the model's claim entirely (there's no file to check
  them against), as are dashed flows — treat both like an inferred
  rationale.
- Function explainers cover Python, JavaScript and TypeScript only, and
  JS/TS discovery is pattern-based: an unusual declaration style may be
  missed, and a symbol the model skips simply has no explainer.
- Annotation mining is capped at the 40 most recently updated merged PRs
  per repo, filtered down to non-trivial ones — a deliberate cost/latency
  tradeoff, not full history. A repo with very little PR history (like
  `scribe-dictation`) will surface only a handful of annotations, which is
  correct behavior (the brief calls for degrading to mostly-null rather
  than fabricating), not a bug.
- Free-tier Ollama Cloud quota is the practical bottleneck on fresh
  analysis of new repos (cached repos are unaffected) — see the Deployment
  section above for how that resets.
- No "generate a build-your-own playbook" feature — out of scope for v1 per
  the build brief.
