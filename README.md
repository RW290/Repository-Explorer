# repo-explorer

Ingests a GitHub repo and produces an explorable, animated visualization of
its architecture, annotated with *why* things are the way they are — pulled
from PR descriptions and linked issues. See
[`repo-breakdown-build-brief.md`](repo-breakdown-build-brief.md) for the full
design.

Priority order: accuracy of the "why" annotations > the animated viewer >
breadth of repos it works on.

## Status

**Phase 1 of 4 (fixture-first)** — the backend serves a hand-written,
synthetic fixture graph (`backend/fixtures/sample_graph.json`, modeled on a
fictional "taskflow" repo) and the frontend renders it with the full
animated pan/zoom viewer. The architecture parser and history miner
(phases 2–3) aren't built yet — `backend/app/llm.py` has the Gemini Flash
call abstraction ready for them.

## Layout

- `backend/` — FastAPI server (Python). `GET /api/graph` returns the graph;
  today that's the fixture, later the parser+miner output.
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

Open the Vite dev server URL (typically `http://localhost:5173`).

## Secrets

`GEMINI_API_KEY` lives in `.env` at the repo root (gitignored, never
committed). Get/rotate it at [aistudio.google.com](https://aistudio.google.com).
`llm.py` refuses to call any model whose name doesn't contain "flash" —
that's the one thing that would turn this from free to billed.

## Next phases

1. ~~Fixture-first viewer~~ (done)
2. Architecture parser: static-analysis dependency graph + batched LLM file
   summaries, run against a real target repo.
3. History miner: GitHub GraphQL PR/issue extraction, batched rationale
   extraction, spot-checked by hand.
4. Merge + full end-to-end pass on the same target repo.
