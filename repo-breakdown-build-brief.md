# Repo breakdown tool — build brief

## Purpose

A tool that ingests a GitHub repo and produces an explorable visualization of its current architecture, annotated with *why* things are the way they are — pulled from commit history, PR descriptions, and linked issues. The point is understanding an existing codebase deeply enough to internalize the patterns and reuse the reasoning in your own projects, not just seeing what the code does.

**Priority order:** accuracy of the "why" annotations > the animated viewer itself > breadth of repos it works on. An animated viewer full of confidently-wrong explanations is worse than a plain one that's honest about what it doesn't know — but the animation is a real requirement, not a nice-to-have deferred to a later pass.

## Scope for v1

- One repo at a time, not a multi-repo dashboard.
- One language for the architecture parser's *target-repo* analysis (pick Python or JS/TS — whichever the repo being analyzed is written in; don't build a multi-language parser yet). This is separate from the tool's own stack, below.
- Current-state architecture graph, enriched with historical annotations. No standalone timeline view.
- No "generate a playbook for building your own version" feature yet — that's a deliberate v2 cut. v1 stops at explaining what's there.
- Single repo, two stacks: backend in Python (parser, miner, API server), frontend in TypeScript/JS (the animated viewer). One repo, e.g. `/backend` and `/frontend` folders, backend exposes a JSON API the frontend fetches from.
- Latency constraint: minimize round-trip API calls, especially to the LLM and to the GitHub API. This shapes the pipeline design below — batch wherever possible rather than one call per file or per PR.
- Input: a GitHub repo URL provided by the user at runtime, not a hardcoded target repo. The backend clones the repo (shallow clone is sufficient — full history isn't needed locally since PR/issue data comes from the API, not `git log`) and runs the pipeline against it on demand.

## Data model

Two top-level entities:

**Graph node** — one per file or module (folder-level nodes can be parent containers).
```json
{
  "id": "src/auth/session.py",
  "type": "file",
  "parent": "src/auth",
  "summary": "LLM-generated one-paragraph description of this file's role",
  "dependencies": ["src/auth/tokens.py", "src/db/client.py"],
  "annotations": ["ann_001", "ann_004"]
}
```

**Annotation** — one per significant historical change attached to a node.
```json
{
  "id": "ann_001",
  "node_id": "src/auth/session.py",
  "source": "pr",
  "source_ref": "PR #142",
  "date": "2024-03-11",
  "rationale_stated": "Author's own explanation, taken from PR description/issue, or null if none given",
  "rationale_inferred": "Model's best guess from the diff alone, only if rationale_stated is null or thin",
  "confidence": "high | medium | low",
  "diff_summary": "One-sentence description of what changed"
}
```

Keep `rationale_stated` and `rationale_inferred` as separate fields, never merged — this is the single most important integrity rule for the whole tool. Anything the model infers without direct textual support must be marked as inferred and get a `confidence: low`, not smoothed into a false-sounding "official" reason.

## LLM backend

> **Update, post-v1:** the Gemini Flash plan below shipped as designed, then
> was replaced twice — first with Hugging Face's hosted Inference Providers
> (`meta-llama/Llama-3.1-8B-Instruct`), then with Ollama Cloud
> (`gpt-oss:20b`), each time behind the same `call_llm(prompt) -> str`
> interface this section specifies, so neither swap touched parser/miner
> logic. Each move was forced by the same constraint that shaped the
> original choice — cost/quota, not quality: Gemini's free tier turned out
> to cap at ~20 requests/*day* (much stricter than advertised, discovered
> mid-build); Hugging Face's free credit is a flat ~$0.10/month; Ollama
> Cloud's quota is GPU-time-based and resets every few hours plus a weekly
> cap, which is the most forgiving of the three for this app's sporadic
> traffic. See `backend/app/llm.py` and the README's Secrets/Deployment
> sections for the current state — the code below is what v1 actually
> looked like, kept as the decision record.

The architecture parser needs no model — dependency graph extraction is static analysis. The file summaries and rationale extraction do need one.

**Cost constraint: this must not cost money to run.** That rules out paid closed-model APIs (Claude/GPT) as the default.

**Decided for v1: Gemini Flash, free tier.** API key already provisioned (`GEMINI_API_KEY` in `.env`, not committed — see below). Implement the LLM call behind a single abstracted interface (one function: prompt in, structured JSON out) so a different backend can be swapped in later without touching parser/miner logic, but build the default flow against Gemini Flash specifically:

```python
import os
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def call_llm(prompt: str) -> str:
    response = client.models.generate_content(
        model="gemini-flash-latest",  # confirm current Flash alias at aistudio.google.com — Google renames/versions these periodically
        contents=prompt,
    )
    return response.text
```

Guard against ever pointing this at a Pro model — that's the one thing that turns this from free to billed. If a model name is configurable, validate it's a Flash variant before making the call.

**Batching pattern** — one call per group of files/PRs, not one call per item, per the latency constraint above:
```python
prompt = f"""
For each file below, return a JSON array of objects with "path" and "summary" fields.
Return ONLY the JSON array, no other text.

Files:
{batched_file_contents}
"""
text = call_llm(prompt).strip().removeprefix("```json").removesuffix("```").strip()
results = json.loads(text)  # wrap in try/except — Flash occasionally deviates from the requested format
```

**Rate limit handling** — Flash's free tier is roughly 15 requests/minute. Wrap every `call_llm` invocation in retry-with-backoff:
```python
import time

def call_with_retry(fn, max_retries=5):
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
```

**Fallback if free-tier limits become a blocker:** fully local (Ollama, quantized 7-8B model) — zero external dependency, no rate limits, but a real quality drop on the stated-vs-inferred rationale distinction at that size. Not the default; only reach for this if Gemini's free tier proves too restrictive in practice.

*(This fallback's instinct was right, its shape wasn't: Ollama did end up being the answer, but as Ollama **Cloud** — hosted GPU inference, not fully local — once "fully local" ran into the same constraint the rest of this section exists to avoid: it means running on this server's or the end user's own hardware, which was later ruled out for the deployed app. See the update note above.)*

Leave the paid closed-model path in the abstraction but don't build the default flow against it.

## Repo setup

- `.env` holds `GEMINI_API_KEY=<key>` and any other secrets. Never commit this file.
- `.gitignore` at the repo root must include at minimum:
  ```
  .env
  __pycache__/
  *.pyc
  node_modules/
  .venv/
  ```
- If the key was ever pasted into a commit before `.gitignore` was in place, it needs to be rotated in AI Studio, not just removed going forward — git history still holds it otherwise.

**Setup checklist, in order:**
1. Create `.gitignore` with the contents above *before* the first `git add`, if this repo hasn't been initialized yet.
2. Create `.env` with `GEMINI_API_KEY=your_actual_key_here` — plain text, no quotes needed around the value.
3. If the repo already exists and you're not sure whether the key was ever committed: run `git status` — if `.env` shows as tracked, run `git rm --cached .env` to stop tracking it going forward.
4. That only fixes tracking going forward. If `.env` (or the key pasted anywhere else — a script, a notebook) was ever actually committed, the key is still in git history even after removal. Rotate the key in AI Studio if there's any doubt, rather than trying to scrub history.
5. If this repo hasn't been pushed to GitHub yet, none of the history concern applies — just get `.gitignore` in place before the first commit.

## Pipeline

### 1. Architecture parser
- Walk the repo tree, build a file-level dependency graph from imports (start with static analysis, e.g. Python's `ast` module or a JS import parser — not a full type-checker). This step is local computation, no API calls involved.
- Generate file summaries via LLM, but batch multiple files into a single call rather than one call per file — group by folder or by a token budget (e.g. as many files as fit in one context window with room for output), and prompt for a JSON array of summaries in one response. This is the single biggest lever on latency, since a repo of any real size means dozens to hundreds of files.
- Output: the graph node list above, no annotations yet.

### 2. History miner
- Pull PRs, not raw commits, as the primary unit (GitHub API). Use the GraphQL API rather than REST here if practical — it lets you fetch a PR's title, description, linked issue, diff stats, and review comments in one request instead of several separate REST calls, and lets you page through many PRs per request.
- For squash-merged repos, check the merge commit body for the original PR description before assuming the signal is lost.
- Filter out trivial diffs before spending LLM calls on them (formatting-only changes, lockfile/dependency bumps, generated files) — a simple heuristic on diff size and file type, done locally with no API call.
- Batch the remaining PRs into LLM calls the same way as the parser step — multiple PRs per call, prompting for a JSON array of extractions — rather than one call per PR. For each PR extract:
  - `rationale_stated`: pull directly from the PR description/issue if one exists; null if the author gave no explanation.
  - `rationale_inferred`: only generate this if `rationale_stated` is null or clearly thin — infer from the diff itself.
  - `confidence`: high if rationale is directly stated, medium if inferred from a clear diff pattern, low if inferred from an ambiguous diff.
- Map each PR's changed files to graph nodes locally (no extra API call — this is just matching file paths already in hand).
- Expect wide variance by repo. A well-maintained OSS repo yields real paragraphs; a scrappy one yields almost nothing — the miner should degrade to mostly-null rather than fabricate.

### 3. Merge
- Attach annotations to their graph nodes. Sort each node's annotation list by date. Output the combined JSON that the viewer consumes.

## Viewer requirements

- Renders the graph with an animated pan/zoom camera: repo level shows folders, clicking a folder smoothly zooms the camera into it and reveals its files, clicking a file zooms further to show its detail. This should feel like navigating into the codebase, not switching between static screens — animate the transform (position + scale) with easing, not a hard cut.
- Level of detail should change with zoom: at the repo level, show folder names only; zoomed into a folder, show file names and maybe a summary line; zoomed into a file, show the full summary, dependencies, and annotations.
- Clicking/selecting a node at any zoom level shows: its summary, its dependencies, and its annotation list (with `rationale_stated` visually distinct from `rationale_inferred` — confidence should be visible at a glance, not just in a tooltip).
- Must handle "no annotations on this node" gracefully (most nodes in most repos will have none) — don't let empty states look broken.
- All graph data (nodes, edges, annotations) should be fetched from the backend once per repo, not re-fetched as the user pans/zooms — the camera movement is client-side animation over already-loaded data, not a source of additional round trips.

## Phasing

1. **Fixture-first.** Hand-write a ~15-20 node JSON graph with a handful of realistic annotations for a repo you know well. Build and sanity-check the viewer against this before any scraping exists.
2. **Architecture parser**, run against a real small-to-medium repo. Validate the dependency graph and summaries look right before adding history.
3. **History miner**, run against the same repo. Spot-check a sample of annotations by hand against the actual PRs — this is the step most likely to produce confidently-wrong output, so budget real review time here, not just a glance.
4. **Merge + full viewer pass** on the same repo end to end.

## Open decisions before Claude Code starts

- **Target repo for v1**: pick something mid-sized (dozens, not thousands, of files), well-documented, with PR discipline (real descriptions, not just "fix") so there's actually signal to mine.

## Explicitly out of scope for v1

- Multi-language support
- Multi-repo comparison
- "Generate a build-your-own playbook" abstraction layer
