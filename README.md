# repo-explorer

Ingests a GitHub repo and produces an explorable, animated visualization of
its architecture, annotated with *why* things are the way they are — pulled
from PR descriptions and linked issues. See
[`repo-breakdown-build-brief.md`](repo-breakdown-build-brief.md) for the full
design.

Priority order: accuracy of the "why" annotations > the animated viewer >
breadth of repos it works on.

## Status

**All 4 phases done for one validated target repo: [psf/requests](https://github.com/psf/requests).**

- **Parser** (`backend/app/parser.py`) — shallow-clones the repo, builds a
  file-level import graph via Python's `ast` module (no LLM), then batches
  files into Gemini Flash calls for summaries.
- **Miner** (`backend/app/miner.py`) — fetches merged PRs via the GitHub
  GraphQL API (through the `gh` CLI, so no raw token is ever handled or
  stored), filters trivial/irrelevant diffs locally, and batches the rest
  into Gemini Flash calls for rationale extraction. Falls back to the merge
  commit body for squash-merged PRs, and only fetches a full diff (via
  `gh pr diff`) for the minority of PRs with no usable description.
- **Merge** (`backend/app/merge.py`) — attaches annotations to nodes.
- **Viewer** — same animated pan/zoom UI from phase 1, now pointed at real
  data. The landing screen lets you type a repo URL; results are cached to
  `backend/.cache/` (gitignored) since a fresh run costs real LLM/API calls.

Spot-checked a sample of `psf/requests` annotations by hand against the
actual PRs (e.g. the CVE-2024-47081 fix, the SSLContext-caching revert in
v2.32.5) — rationale text matched the real PR content, and thin/missing
PR descriptions correctly fell back to `rationale_inferred` with lower
confidence rather than being presented as stated fact.

Only `psf/requests` has been validated end-to-end. The parser's import
resolution is written generically (absolute + relative imports, `src/`
layout awareness) but only tested against that one repo's layout — other
repos, especially non-`src`-layout or heavily dynamic-import codebases,
may need adjustments.

## Layout

- `backend/` — FastAPI server (Python).
  - `GET /api/graph` — the phase-1 fixture, zero cost/latency.
  - `GET /api/graph?repo_url=https://github.com/owner/repo` — runs the real
    pipeline (cached after the first run per repo).
- `frontend/` — React + TypeScript + Vite viewer with an animated pan/zoom
  camera: repo level shows folders, clicking a folder zooms into its files,
  clicking a file zooms into full detail (summary, dependencies,
  annotations with stated-vs-inferred rationale visually distinguished).

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
that's the one thing that would turn this from free to billed.

GitHub API access goes through the `gh` CLI's own stored auth (`gh auth
login`) rather than a token in `.env` — nothing GitHub-related needs a
secret checked in or copy-pasted.

## Known limitations / not yet done

- Only tested against one repo (`psf/requests`). No multi-language support,
  by design (v1 scope, Python only).
- The viewer's zoom model is 2 levels deep (folder → file); a repo with
  deeply nested subpackages gets flattened one level, which is fine for
  `requests`'s shallow layout but would lose structure on a deeply nested
  repo.
- Annotation mining is capped at the 40 most recently updated merged PRs
  per repo, filtered down to non-trivial ones — a deliberate cost/latency
  tradeoff, not full history.
- No "generate a build-your-own playbook" feature — out of scope for v1 per
  the build brief.
