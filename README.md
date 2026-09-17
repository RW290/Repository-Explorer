# Repository-Explorer

Point it at a public GitHub repository and it builds an explorable picture of
the codebase: which files exist, how they depend on each other, what each one
is for, how they group into architectural layers, and — drawn from the
project's own pull requests — *why* things are the way they are.

**Live**: [repository-explorer.replit.app](https://repository-explorer.replit.app)

[`DECISIONS.md`](DECISIONS.md) is the decision log: what was decided, why,
what else was considered, the evidence, and how to challenge it.
[`AI_ENGINEERING.md`](AI_ENGINEERING.md) walks through the AI engineering
ideas used here — context engineering, structured output and validation,
grounding, cost and latency control, reliability around a remote model —
where each lives in the code, what it costs, and which well-known techniques
are deliberately not used.

It is built for an engineer reading an unfamiliar codebase to learn from it,
so every piece of model-written text explains design rationale and
tradeoffs rather than restating code. The priorities, in order: accuracy of
the "why" > the viewer > breadth of repos it handles.

## The integrity rule

Anything the model *infers* is kept visibly separate from anything a human
*stated*, everywhere:

- A PR annotation has two fields that are never merged: `rationale_stated`
  (the author's own reason, restated precisely) and `rationale_inferred`
  (the model's reading of the diff, only when the author gave none), plus a
  `confidence`. The viewer renders them differently.
- On the architecture map, a flow the import graph backs is drawn solid; one
  the model asserted but imports can't show (an HTTP call, a subprocess, an
  external API) is drawn dashed and labeled "inferred".
- Every box on the map is a real file or folder. A path the model gives that
  doesn't exist is dropped, never guessed at.
- Where there is no signal — a repo with thin PR history — the tool shows
  less rather than inventing more.

## How an analysis runs

`backend/app/pipeline.py` orchestrates it:

```
clone → resolve imports → ┬ summarize files      ┐
                          ├ mine pull requests   ├→ merge → map architecture → cache
                          └ write overview       ┘
```

**Structure first, and published immediately.** Cloning and import
resolution need no model and take a few seconds. The pipeline publishes that
partial graph right away and re-publishes as summaries, the overview and PR
history land, so the viewer opens on a real graph within seconds and fills
in live. Time-to-first-useful-view is decoupled from time-to-finished. On
psf/requests (121 files, 40 PRs, free tier) the graph is explorable at about
1.5s and the analysis completes in about 3½ minutes.

The three model-bound stages in the middle are independent — history needs
only the file list, the overview only the README — so they run concurrently.
The map goes last because it is the one stage that reads the summaries.
Finished graphs are cached to `backend/.cache/` (gitignored); a cached repo
opens instantly.

### Import resolver — `backend/app/imports.py`

The dependency edges. Local static analysis, no model: Python via `ast`,
everything else via line-anchored import patterns. Import statements are the
most regular syntax a language has, and a real parser per language would
mean a native toolchain per language on the server; the substance is
*resolution*, which is language semantics:

| Language | What resolves |
|---|---|
| Python | Relative imports, packages, and import roots inferred from layout (repo root, `src/`, project directories, the file's ancestors), so a nested package such as `backend/app` resolves. Standard-library names never match a repo file. |
| JS / TS / Vue / Svelte | `import`, `export … from`, `require`, dynamic `import()`; extension and index resolution; the `.js`→`.ts` ESM convention; tsconfig/jsconfig `paths` and `baseUrl` (with `extends` and JSONC comments); workspace packages; Node `#subpath` imports; the `@/`→`src/` convention |
| Go | `go.mod` module path → package directory → its files |
| Rust | `mod` declarations; `crate::`, `super::`, `self::` paths; sibling workspace crates; a binary's use of its own library crate |
| Java / Kotlin / Scala / Groovy | Fully-qualified, static and wildcard imports, matched by path suffix |
| C / C++ / Objective-C | `#include`, relative first, then by path suffix |
| Ruby, PHP, Dart | `require`/`require_relative`; composer PSR-4 `use` and literal includes; relative and own-`package:` imports |
| CSS / SCSS / Less, HTML | `@import`/`@use`/`@forward` including Sass partials; `<script>`/`<link>` references |

An edge exists only when an import lands on a file in the repo. Third-party
and standard-library imports resolve to nothing and are dropped, which keeps
the graph a picture of this codebase rather than of its dependency tree.

Resolver output is versioned (`RESOLVER_VERSION`, stored on each graph).
Opening a graph produced by an older resolver re-resolves its edges from a
fresh clone in a background job — seconds, no model calls — and re-checks the
map's solid/dashed verdicts, leaving the expensive parts of the analysis
intact.

### File summaries — `backend/app/parser.py`

Generation time tracks how much the model *writes*, so files are tiered:

- **Source files** get a paragraph about their design, grounded in what they
  import and what imports them.
- **Supporting files** (docs, config, data, tests, templates) get a sentence
  or two.
- **Boilerplate and empty files** (a LICENSE, `.gitignore`, an empty
  `__init__.py`) get a templated summary and no model call.

Batches are sized by input budget rather than a fixed file count, so small
files share a call. Most-connected files are summarized first, since results
stream to the viewer and hubs are what a reader opens first. A reply that
isn't valid JSON is salvaged entry by entry and only the missing files are
asked for again. Lockfiles, vendored and generated output, and binary, media
and design-tool files are excluded from analysis entirely
(`EXCLUDE_*` in `parser.py`).

The same module writes the **project overview** from the README (or the
folder structure when there is none).

### PR history — `backend/app/miner.py`

Fetches the 40 most recently updated merged PRs through the GitHub GraphQL
API in one round trip per page, drops trivial ones locally (formatting,
lockfile bumps, changes touching no known file), and extracts rationale from
the rest in batches. For squash-merged repos the merge commit body is
checked for the original description. A diff is fetched only for the
minority of PRs with no usable description. Transient network failures are
retried; configuration failures (no token, private repo) surface at once.
`merge.py` attaches the resulting annotations to the files each PR touched.

### Architecture map — `backend/app/architecture.py`

A semantic view laid over the file graph, in the spirit of
[gitdiagram](https://github.com/ahmedkhaleel2004/gitdiagram). Given the file
summaries and the real import edges, the model picks the 8–20 files and
folders a reader needs, sorts them into 2–6 groups by architectural role
(frontend / HTTP API / core / LLM / tests …), names the main flows between
them, and may add up to six external systems the code talks to. It returns a
bounded JSON AST, never Mermaid; the frontend compiles it deterministically.

The validator resolves every member to a real node, drops what it can't,
caps the counts so the diagram stays a map rather than a second file
listing, removes edge labels that say nothing ("imports", "uses"), and marks
each flow as import-backed or not. A second model call is made only when the
first result is unusable or lost a quarter of itself to validation.

`POST /api/repos/{owner}/{name}/architecture` builds a map for a cached
graph that has none.

### Function explainers — `backend/app/symbols.py`

The first time a file's source is opened, every function, method and class
in it gets a one-line explanation of what it does and why it exists. Symbols
are found locally — Python via `ast` (including nested definitions and
`Class.method` names), JavaScript/TypeScript via conservative line patterns;
other languages get none rather than guesses — and explained in one batched
call per ~30 symbols. The result is stored per file, so a file costs one
generation and every later visitor reads the stored set.

### Ask why — `backend/app/explain.py`

Four kinds of on-demand question, each answered once and then shared with
everyone who opens that repo (stored by `rationale_store.py`, alongside the
function explainers):

- about a highlighted range of code;
- about why a whole file exists, grounded in what it depends on and what
  depends on it;
- follow-ups about the project overview;
- about the **architecture map**. The model is handed the map itself —
  each group with its member files and what they do, the external systems,
  and every flow with whether imports back it — plus whatever the reader has
  selected, so "explain this section" has a referent and the answer is about
  this diagram rather than architecture in general. A general concept
  question (a pattern, a protocol, a term) gets the concept explained and
  then located in this repo; a question resting on a mix-up gets the
  distinction untangled rather than played along with; an answer that leans
  on an inferred flow says so.

### One audience — `backend/app/audience.py`

Every prompt that produces reader-facing text shares one framing: the reader
is a software engineer learning system design, so answers explain why an
approach was chosen over alternatives and what it costs (latency,
scalability, coupling, failure modes) where that is genuinely relevant, use
precise terms, and say what is knowable instead of guessing.

### Talking to the model — `backend/app/llm.py`

One function, `call_llm(prompt) -> str`, in front of an open-weight model
(`gpt-oss:20b` by default) served by Ollama Cloud. Swapping providers means
changing that function's body. How it calls matters more than which model:

- **Reasoning effort is the largest latency lever.** At default effort the
  model writes roughly 9,000 characters of hidden reasoning to produce a
  5,000-character batch of summaries; at `think="low"` it writes about 50,
  in half the time, with summaries of the same quality. Every call site
  runs low. Each setting is checked by the eval harness rather than by eye:
  for PR rationale, both efforts score the same on every check and read the
  same side by side, and low is about 25% faster.
- **Concurrency is capped at 2**, by one process-wide semaphore
  (`LLM_CONCURRENCY`) so simultaneous analyses share the ceiling. The
  provider mostly queues concurrent requests rather than running them in
  parallel, so a larger pool only lengthens the queue.
- **Responses are streamed, with a 240s stall timeout.** A non-streaming
  call sends nothing for the whole generation, and a connection silent for
  minutes can be dropped along the path without notice, leaving the client
  waiting forever. Streaming keeps bytes moving, and silence becomes a
  retryable failure. The window is long enough to outlast queue wait at the
  concurrency above; shorter would kill requests that are only waiting
  their turn.
- **Every call has a token budget.** A reasoning model occasionally falls
  into a repetition loop and generates until its context is exhausted. Each
  caller states roughly how long a good answer is; that becomes a
  server-side `num_predict` cap and a client-side ceiling. Tripping either,
  or a 5-minute deadline, is retried — a fresh sample is almost always fine.
- **Only transient failures are retried.** A bad key or a retired model
  surfaces immediately with an explanation instead of burning backoff
  attempts.

### Errors — `backend/app/errors.py`

Every failure that can reach a user is a sentence saying what broke, why,
and what to do about it. The layers that touch the outside world — GitHub,
Ollama Cloud, `git clone` — each translate their own failure modes: missing
credentials, an invalid or expired key, a private or misspelled repo, a
retired model, an exhausted quota, a connection that went silent. Anything
unrecognized is labeled plainly as a bug in this tool rather than dressed up
as user error.

## The viewer — `frontend/`

React + TypeScript + Vite. The landing page takes a repository URL (or opens
a built-in fixture demo that needs no network or credentials).

**While an analysis runs**, a stage tracker shows all six stages with
status, counts and timings, plus a feed of recent events — first as a card
on the landing page, then, once the partial graph arrives a second or two
later, as a collapsible HUD over the viewer. Cards whose summary isn't
written yet show a shimmer, and the map tab unlocks when the last stage
lands. Polling runs about once a second; the backend sends the graph back
only when its version changed, so most polls are a few hundred bytes.

A **Map / Folders** switch sits beside the breadcrumbs at every level (or
press `M`), and it keeps your place: from a file, Map selects that file on
the map; from a selected map node, Folders lands on that node in the
explorer.

- **Map** (`ArchitectureView.tsx`, compiled by `mermaid.ts`) — the
  architecture map as a Mermaid flowchart laid out by ELK, on a freely
  pannable and zoomable canvas: drag to pan, wheel or pinch to zoom, fit and
  zoom controls. Both layout directions are tried and the one that fits the
  viewport larger is kept. Each node shows its name, folder path and the
  first line of its summary; external systems are greyed hexagons. Clicking
  a node opens the detail panel with its group and its in/out flows, each
  tagged import-verified or inferred. **Ask** (in the top bar, or "Ask about
  this on the map" in the detail panel) opens a question panel beside the
  map, scoped to whatever is selected: a file, a group box — the groups are
  clickable, and clicking one opens the panel on it — or the whole map.
  The map moves aside for the panel and refits, unless the reader has
  already zoomed or panned, in which case their view is kept. An "all imports" toggle overlays every
  other real import between the map's nodes, so the model's chosen flows can
  be checked against the whole truth. Mermaid runs in strict security mode
  with no click directives; node clicks are wired onto the rendered SVG.
- **Folders** (`Viewer.tsx`, `layout.ts`) — an animated camera over the file
  graph: folders at the top with the project overview, a folder's files one
  level in.

**Moving around is the same in both**: the wheel (or a two-finger scroll)
zooms at the cursor, a trackpad pinch zooms, and dragging moves. The wheel
always zooms rather than sometimes panning, because a smooth-scrolling mouse
and a trackpad send indistinguishable events. In the folder view this free
camera sits on top of the animated one: the reader's own zoom and position
hold until they navigate, then the camera eases to the new target, and
"recenter" returns to it at any time. A drag that ends over a card is a
move, not a click.

**Clicking a file opens its source**, from the map or from a folder — there
is no details step in between. The source viewer (`SourceViewer.tsx`) is the
one place for everything about a file: syntax-highlighted code with a
function explainer above each definition, and a side bar with three tabs.
*Overview* has the summary, "why does this file exist?", the file's group
and flows on the map, what it imports and what imports it (both are links,
so reading can follow the import graph from file to file), and its PR
history, with stated and inferred rationale styled differently and
confidence visible at a glance. *Functions* lists every function with its
explainer and jumps to it. *Ask why* is the highlight-to-ask thread;
highlighting code switches to it. `Esc` closes. Folders and external
systems, which have no source, get a details panel instead
(`DetailPanel.tsx`), as does every node in the fixture demo.

**Anything still loading is drawn as a skeleton** (`Skeleton.tsx`) —
greyed, shimmering placeholders in the shape of what is coming: the whole
viewer while a repository opens, the map while it is laid out or built, code
lines while a file is fetched, function rows while explainers are written,
cards and the project overview while a live analysis fills them in, and
answer text while a question is out. A spinner says "wait"; a skeleton says
what is about to appear and where, and keeps the layout from jumping when it
does.

Files are coloured by kind (`languages.ts`): a hue per language, quieter
tones for docs and config, generated per theme so both light and dark stay
legible. The legend lists the kinds on screen. Mermaid, ELK and the syntax
highlighter are lazy-loaded, so the landing bundle carries none of them.

## Data model

One `Graph` per repository (`backend/app/models.py`, mirrored in
`frontend/src/types.ts`):

```jsonc
{
  "repo_url": "https://github.com/owner/name",   // null for the fixture demo
  "overview": "…",
  "resolver_version": 2,
  "nodes": [{
    "id": "src/auth/session.py",                 // repo-relative path
    "type": "file",                              // or "folder"
    "parent": "src/auth",
    "summary": "…",
    "dependencies": ["src/auth/tokens.py"],      // resolved import edges
    "annotations": ["ann_001"]
  }],
  "annotations": [{
    "id": "ann_001", "node_id": "src/auth/session.py",
    "source": "pr", "source_ref": "PR #142", "date": "2024-03-11",
    "rationale_stated": "…",                     // or null
    "rationale_inferred": null,                  // only when stated is null
    "confidence": "high",                        // high | medium | low
    "diff_summary": "…"
  }],
  "architecture": {                              // null if none was produced
    "groups": [{ "id": "core", "label": "Core HTTP", "description": "…" }],
    "nodes":  [{ "id": "src/auth/session.py", "group": "core" },
               { "id": "ext:redis", "group": null, "external": true,
                 "label": "Redis", "description": "…" }],
    "edges":  [{ "source": "…", "target": "…", "label": "reads cache",
                 "backed": true }]
  }
}
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check for the hosting platform |
| `GET /api/graph` | The fixture demo graph |
| `GET /api/graph?repo_url=…` | Synchronous analysis. Fine locally; a hosted deployment should use the job endpoints, since a request open for minutes gets cut off by most platforms |
| `POST /api/analyze` `{repo_url, force_refresh?}` | Returns the cached graph, or starts a background job and returns its `job_id` |
| `GET /api/analyze/{job_id}?have=N` | Job `status`, `progress` (stages, events, partial-graph version) and the graph — partial while running, omitted when the client already has version `N` |
| `POST /api/repos/{owner}/{name}/architecture` | Build the map for a cached graph that has none |
| `POST /api/repos/{owner}/{name}/symbol-explainers` `{path}` | Function explainers for a file; generated on first request, stored after |
| `GET /api/repos/{owner}/{name}/file?path=…` | A file's source, fetched from GitHub on demand |
| `GET`/`POST …/rationales`, `…/file-rationales`, `…/project-rationales`, `…/architecture-rationales` | Read stored answers, or ask a question about a code range, a file, the project, or the architecture map (optionally focused on a group or node) |

In production the same FastAPI process also serves the built frontend.

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

Open the Vite URL it prints. Paste a GitHub repository URL to analyze it, or
choose "Try the interactive demo instead". The dev server proxies `/api` to
the backend on port 8000. `start.sh` runs both together, and is what the
Replit workspace uses.

Run the backend with `--reload`: a stale API process behind a fresh frontend
answers "Method Not Allowed" on any endpoint it doesn't have yet.

Tests (import resolution asserting exact edge sets per language, and the
eval scorers):

```bash
cd backend && python -m unittest discover -s tests
```

## Evaluating model output

Unit tests can't say whether a summary is *good*. The eval harness
(`backend/evals/`) measures it, repeatably:

```bash
cd backend
python -m evals freeze psf/requests        # save a repo's inputs (no model calls)
python -m evals check-graph .cache/psf__requests.json   # score a finished analysis (free)
python -m evals run prs --variant default --variant low:think=low
python -m evals run summaries --variant prod --sample 8
python -m evals judge summaries low default --sample 6
python -m evals accept evals/results/<file>.json low    # record a baseline
```

- **Frozen cases** (`evals/cases/`): the exact files, pull requests and
  summarized graph each model call site sees, pinned to a commit and checked
  in, so two runs differ only in what was changed on purpose.
- **Suites** run one call site — file summaries, PR rationale, the
  architecture map — with the production prompt and parser under a named
  *variant* (reasoning effort, temperature, model). Evaluating a call site
  rather than the whole pipeline keeps a run to a handful of calls and makes
  a moved number attributable to one prompt.
- **Scorers** (`checks.py`, unit-tested) are plain code, each named for a
  failure that has actually happened here: coverage (a malformed reply
  blanking a batch), summary length per tier, verbatim overlap with the PR
  author (quoting instead of stating the reason), stated and inferred never
  both set, confidence matching kind, map members that resolve to real files,
  empty edge labels, share of flows backed by imports. Hand labels in a
  case's `labels.json` add accuracy against a person's judgment.
- **A model judge** (`judge.py`) compares two variants' summaries pairwise
  for what code can't score — accuracy to the file, explaining design. It
  uses a stronger model than the one being judged, randomizes order, and
  reports how often position A won, which should sit near 50%.
- **Gates** are absolute floors that fail the run; a saved **baseline**
  shows drift in numbers that still pass. A provider outage is reported as
  such, not scored.

[`DECISIONS.md`](DECISIONS.md) records what these runs have decided so far.

## Configuration

Secrets and settings come from the environment; locally, from a gitignored
`.env` at the repo root (see `.env.example`).

| Variable | Needed | Purpose |
|---|---|---|
| `OLLAMA_API_KEY` | Always | Ollama Cloud key, free at [ollama.com/settings/keys](https://ollama.com/settings/keys). Free-tier usage is metered by GPU time and resets every few hours, with a weekly cap |
| `OLLAMA_MODEL` | Optional | Model override; default `gpt-oss:20b`, the smallest free-tier model, which stretches a time-based quota furthest |
| `LLM_CONCURRENCY` | Optional | Concurrent model calls, process-wide; default `2` |
| `GITHUB_TOKEN` | When deployed | Personal access token with public-repo read access. Locally the backend uses the `gh` CLI's own login (`gh auth login`), so no token needs to exist on disk |
| `CORS_ORIGINS` | When the frontend is on another origin | Comma-separated allowed origins; default `http://localhost:5173` |
| `VITE_API_BASE` | When the backend is on another origin | Set in `frontend/.env` at build time; default is same-origin |

## Deployment

A single Replit **Autoscale** service: one FastAPI process serves both the
API and the built frontend, and scales to zero when idle. Traffic is
sporadic, so paying per use (with a Replit spending limit as a ceiling)
beats a Reserved VM's flat cost. The price is cold starts: the first request
after an idle period waits a few seconds for an instance to boot, which
shows in the deploy logs as a transient failed health check.

Build: `cd frontend && npm ci && npm run build`. Run: uvicorn on port 5000.
See `replit.md` for the workspace setup. Pushing to GitHub does not update
the live site; it is republished from Replit's Deploy panel.

Operational facts worth knowing:

- **The model quota is shared** by everyone using a deployed instance, and
  is the practical limit on analyzing new repos. Cached repos are
  unaffected.
- **Job state is in memory** (`jobs.py`). A restart loses the status of
  in-progress jobs; finished graphs are safe in the cache. Running several
  backend instances behind a load balancer would need this moved to shared
  storage first.
- **The cache is the instance's local disk**, so it is not shared between
  instances and should not be assumed to survive a redeploy.
- **There is no authentication or abuse protection** on `/api/analyze`:
  anyone who can reach it can spend the quota on any public repo. Suitable
  for a personal deployment, not an indexed public service.

## Limitations

- **Dependency edges are static and best-effort.** C# and Swift get none,
  because their imports name a namespace that can span any number of files.
  Go files in the same package reference each other with no import
  statement, so a single-package Go repo legitimately shows no edges. A Go
  import or JVM wildcard names a whole directory, and the fan-out to its
  files is capped at 8. Anything resolved at runtime — `importlib`,
  `require(variable)`, dependency injection, reflection, bundler aliases
  declared only in a Vite or Webpack config — is invisible. These show up as
  missing edges, never wrong ones.
- **The map is the model's judgment.** Which files matter and which layer
  each belongs to can be bounded by the validator (real nodes, real import
  edges) but not verified. External systems and dashed flows are the model's
  claim entirely; read them like an inferred rationale. Two analyses of the
  same repo can produce different maps.
- **Function explainers** cover Python, JavaScript and TypeScript. JS/TS
  discovery is pattern-based, so an unusual declaration style can be missed,
  and a symbol the model skips simply has no explainer.
- **PR history is a window, not the whole record**: the 40 most recently
  updated merged PRs, filtered to substantive ones. A repo with little PR
  discipline surfaces few annotations, which is the intended behaviour.
- **The folder explorer is two levels deep** (folder → file); deeper
  subpackages are flattened into their top-level folder.
- **One repository at a time**, public GitHub repositories only.
