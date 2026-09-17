# AI engineering in Repository-Explorer

A map of the AI engineering ideas this codebase uses, where each one lives,
why it is there, and what it costs. It also says plainly which well-known
techniques are *not* used, because knowing why something is absent teaches
as much as knowing why something is present.

Read it alongside the code: every section names the file to open.

## What kind of AI system this is

An **LLM pipeline application**. A fixed sequence of stages, some of which
call a language model with a prompt built by code, and all of whose outputs
are checked by code before anyone sees them. The model is a component with a
narrow job at each call site, not the thing in charge.

That places it apart from three other common shapes:

| Shape | What decides the next step | Used here? |
|---|---|---|
| LLM pipeline | Your code, in a fixed order | **Yes** |
| RAG application | Your code, after retrieving relevant text by similarity | No |
| Agent | The model, choosing tools in a loop | No |
| Fine-tuned model | Nothing at runtime; behaviour is trained in | No |

The single most important design decision follows from that: **the model
never gets to be the source of truth about anything code can determine.**

---

## 1. Use code for what code can do

**The idea.** A language model is slow, costs money, varies between runs and
can be wrong. Code is instant, free, repeatable and either right or visibly
broken. So the first question at every step is whether a model is needed at
all.

**Here.** The dependency graph is pure static analysis
(`backend/app/imports.py`): Python's `ast` for Python, import patterns plus
per-language resolution rules for everything else. No model is involved in
deciding what imports what. The same goes for discovering files, finding the
functions in a file (`symbols.py`), filtering out trivial pull requests
(`miner.py`), and compiling the architecture map into a diagram
(`frontend/src/mermaid.ts`). The model is reserved for the genuinely
linguistic work: saying what a file is *for*, restating why a change was
made, choosing which files matter.

**Tradeoff.** Static analysis has blind spots a model might paper over
(runtime imports, dependency injection). The choice here is that those show
up as *missing* edges rather than as plausible invented ones. A gap the
reader can see is better than a guess they can't detect.

## 2. Context engineering: deciding what the model reads

**The idea.** A model answers from what is in its prompt. Choosing that
content — what to include, how much of it, in what order, and what to leave
out — usually matters more than the wording of the instruction. This is the
discipline people now call context engineering.

**Here.**

- *Relevant structure, not just raw text.* A file's summary prompt includes
  the list of files it imports (`parser._summary_prompt`), so the model can
  say why the file is scoped the way it is. A "why does this file exist"
  question includes both what the file depends on and what depends on it
  (`explain._file_prompt`).
- *The thing being asked about, serialized for the model.* An architecture
  question gets the whole map rendered as text — groups, members with a
  one-line role each, every flow tagged verified or inferred
  (`explain.describe_map`) — so the answer is about this diagram and not
  about software in general.
- *A referent for "this".* Whatever the reader has selected becomes a focus
  block in the prompt (`explain._focus_block`), which is what makes "explain
  this section" answerable.
- *Importance ordering under a budget.* The architecture prompt can't hold
  every file, so files are sorted by how connected they are and cut off at a
  character budget (`architecture._describe_graph`). What survives the cut
  is what matters most.

**Tradeoff.** Everything added to the context costs input tokens and gives
the model more to be distracted by. The rule used here: include what changes
the answer, summarize what merely informs it, omit the rest.

## 3. Batching, truncation and budgets (and how this differs from chunking)

**The idea.** Every model call has fixed overhead: the instructions, the
framing, the model's warm-up. Sending one item per call pays that overhead
over and over. Batching amortizes it.

**Here.**

- *Batching by size budget.* Files are grouped into calls by total input
  size, not a fixed count, so twenty tiny config files share one call while
  eight large source files fill another (`parser._plan_batches`). Pull
  requests go five to a call; functions thirty to a call.
- *Truncation.* Each file contributes at most its first 3,000 characters
  (1,200 for supporting files). A long function is explained from its first
  30 lines, because a function's intent lives in its head.
- *Output budgets too.* Each call declares roughly how long a good answer
  is, which caps generation (see section 7).

**Is this chunking?** Not in the usual sense, and the difference is worth
being precise about. *Chunking* normally means splitting documents into
pieces (often overlapping), computing an embedding for each, storing them in
a vector index, and at question time retrieving the few pieces most similar
to the question. That is the core of retrieval-augmented generation. This
codebase does none of it: there are no embeddings, no index and no
similarity search. What it does is **budgeted context packing**: take the
head of each item, pack as many items as fit, and order by importance. It
shares chunking's motive (fit useful text into a finite window) but not its
mechanism.

**Tradeoff.** Head-truncation is cheap and works because a file's purpose is
usually legible from its top (imports, docstring, main definitions). It fails
on a file whose most important logic is on line 900. Real chunking with
retrieval would fix that, at the cost of an embedding model, an index, and a
retrieval-quality problem to debug.

## 4. Prompt design

**The idea.** A prompt is an interface specification. Good ones state the
audience, the task, the output contract and the failure behaviour.

**Here.**

- *One shared system framing.* Every reader-facing prompt includes the same
  paragraph (`audience.py`): the reader is an engineer learning system
  design, so explain why an approach was chosen and what it costs, use
  precise terms, and say what is knowable rather than guess. One place to
  change the product's voice, and consistency across six kinds of output.
- *An explicit output contract.* "Return ONLY a JSON array of objects with
  `path` and `summary`", with a literal example of the shape
  (`architecture._prompt` shows a full example object).
- *Negative instructions where the model has a known bad habit.* "A label
  like 'imports' or 'uses' carries no information and is not allowed."
  "Never recite a group's whole member list." These exist because the model
  did exactly those things.
- *Behaviour for the awkward cases.* The architecture-question prompt says
  what to do with a general concept question (explain it, then locate it in
  this repo) and with a question built on a mix-up (untangle the mix-up
  kindly, since that correction is the useful part).
- *Length as a specification.* "One or two sentences", "3-7 sentences of
  plain prose, no heading". Length limits are the cheapest cost control
  there is.

**Tradeoff.** Instructions are requests, not guarantees. Anything that
actually matters is also enforced in code (sections 5 and 6).

## 5. Structured output, validation and repair

**The idea.** When a model's output feeds code, ask for a data structure,
then treat what comes back as untrusted input: parse it defensively,
validate it against reality, and have a plan for when it's wrong.

**Here.**

- *The model returns a JSON AST, never drawing syntax.* For the architecture
  map it produces groups, members and flows as JSON
  (`architecture.py`); a deterministic compiler turns that into a diagram.
  The model cannot emit malformed Mermaid or smuggle in a click handler,
  because it never writes Mermaid.
- *Validation against ground truth.* Every member path must resolve to a
  real node in the parsed graph or it is dropped; counts are capped; ids are
  normalized; empty labels are removed (`validate_architecture`).
- *Repair loop, used sparingly.* If validation discards a quarter of the
  result, the problems are fed back for one more attempt. A couple of dropped
  edges is the validator working, not a failure worth a second call.
- *Tolerant parsing and salvage.* Replies wrapped in code fences or prose
  are unwrapped; a JSON array cut off mid-way is recovered entry by entry,
  and only the files still missing are asked for again
  (`parser._parse_summaries`).

**Tradeoff.** This app parses free-text JSON rather than using a provider's
constrained-decoding or function-calling mode, which would guarantee valid
JSON. The choice keeps `call_llm` a plain string-in, string-out function
that works with any provider; the price is the salvage code.

## 6. Grounding, provenance and honest uncertainty

**The idea.** The dangerous failure of a language model is not nonsense, it
is confident, plausible fabrication. The defence is to tie every claim to
something checkable and to show the reader which claims are which.

**Here.** This is the product's central rule, applied in three places:

- *Stated versus inferred, never merged.* A PR annotation carries
  `rationale_stated` (the author's own reason) and `rationale_inferred` (the
  model's reading of a diff, only when the author gave none) as separate
  fields with a confidence level (`models.py`), and the UI styles them
  differently.
- *Verified versus asserted flows.* Every edge on the map is checked against
  the import graph. Backed ones draw solid; ones the model asserted but
  imports can't show draw dashed and are labeled "inferred"
  (`architecture._mark_backed`). An answer that leans on an inferred flow is
  told to say so.
- *Permission to not know.* The shared framing ends with: if there isn't
  enough context to responsibly infer a rationale, say what is knowable
  instead of guessing. A repo with thin PR history surfaces few annotations
  rather than invented ones.

**Tradeoff.** Calibrated honesty makes the output look less impressive. A
map full of solid arrows and confident rationale demos better than one with
dashed lines and gaps. It is also worse, because the reader can't tell which
parts to trust.

## 7. Controlling inference: effort, temperature, length

**The idea.** A model call has knobs beyond the prompt. With a reasoning
model, the biggest one is how much it thinks before answering.

**Here** (`llm.call_llm`):

- *Reasoning effort.* At default effort the model writes roughly 9,000
  characters of hidden reasoning to produce a 5,000-character batch of
  summaries; at `think="low"` it writes about 50, in roughly half the time,
  with summaries of the same quality. The architecture map takes about 17
  seconds at low effort against about 300 at default. Summarizing is recall
  and phrasing, not deduction, so the reasoning bought nothing.
- *Matching effort to the task, by measurement.* PR rationale keeps default
  effort. At low effort its labels came out the same but its text degraded
  to quoting the author ("Honestly I have no idea why this lib used
  netloc…") instead of stating the engineering reason. The setting for each
  task was chosen by running both and reading the outputs, not assumed.
- *Temperature.* Structured tasks (the map, function explainers) run at 0.2
  so two analyses of one repo resemble each other. Prose runs at the default.
- *Output caps.* Every call passes `max_tokens`, enforced by the provider.

**Tradeoff.** Lower effort is a quality risk that is invisible until you
compare outputs side by side. The discipline is to measure per task.

## 8. Cost and latency engineering

**The idea.** For a generative model, cost and latency track **output
tokens**, because tokens are generated one at a time. Reading is cheap;
writing is expensive. Every saving below is a way of writing less, or not
calling at all.

**Here.**

- *Tiering (routing by value).* Source files get a paragraph; docs, config
  and tests get a sentence or two; a LICENSE or an empty `__init__.py` gets a
  templated string and no model call (`parser.SUMMARY_TIERS`,
  `_templated_summary`). Most of a typical repo is not source code, so this
  is most of the saving.
- *Lazy generation.* Function explainers and every kind of "ask why" answer
  are produced only when someone opens that file or asks that question, never
  during analysis. Work nobody looks at is never done.
- *Memoization.* Whatever is generated is stored and shared: a finished
  analysis in `backend/.cache/`, explainers and answers per repo
  (`rationale_store.py`). A file's explainers cost one generation, ever, for
  everyone.
- *Recompute only what changed, and only with the cheap tool.* Graphs record
  which version of the import resolver produced their edges. When the
  resolver improves, opening an old graph re-derives just the edges from a
  fresh clone, with no model calls, instead of re-running the analysis
  (`pipeline.refresh_dependencies`).
- *Overlap independent work.* PR mining needs only the file list and the
  overview only the README, so both run beside the summaries rather than
  after them.

In practice these took a full analysis from roughly 5% of the free tier's
quota to roughly 0.5%.

**Tradeoff.** Tiers and templates assume you can judge a file's importance
from its type. A critical piece of logic in a `.yaml` file gets a sentence
like any other config file.

## 9. Concurrency, rate limits and backpressure

**The idea.** A hosted model is a shared, rate-limited resource. Firing more
requests at it does not make it faster past a point, and can make it slower.

**Here.** One process-wide semaphore caps in-flight model calls at 2
(`LLM_CONCURRENCY`). It is global rather than per-analysis so that two
simultaneous analyses share the ceiling instead of doubling it. The number
comes from measurement: four concurrent batches finish in about 122 seconds
against about 160 back-to-back, so the provider mostly queues requests
rather than running them in parallel. There is perhaps 1.5x to be had and no
more; a larger pool only lengthens the queue.

**Tradeoff.** The cap is tuned to one provider's free tier. A paid tier with
real parallelism would want a higher number, which is why it is an
environment variable.

## 10. Reliability around a remote model

**The idea.** A model call is a long network request to a service you don't
control. It fails in ways a database call doesn't.

**Here** (`llm.py`):

- *Streaming, for liveness rather than display.* A non-streaming call sends
  nothing for the whole generation, and a connection silent for minutes can
  be dropped along the path without either end being told, leaving the
  client waiting forever. Streaming keeps bytes moving, and silence becomes
  a detectable failure. Nothing is shown to the user token by token; the
  stream exists to keep the socket alive.
- *A stall timeout sized to the queue.* 240 seconds, long enough to outlast
  the wait for a turn at the provider. A shorter one kills requests that are
  merely queued and sends each retry to the back of the line.
- *A runaway guard.* A reasoning model occasionally falls into a repetition
  loop and generates until its context is full. It is a sampling accident,
  so the defence is a fuse, not a prompt fix: a provider-enforced token cap,
  a client-side character ceiling, and a five-minute deadline.
- *Retry only what can succeed.* Timeouts, overload and rate limits are
  retried with exponential backoff. A bad key or a retired model is not,
  because it will not fix itself in fifteen seconds (`errors.py` separates
  transient from configuration failures).
- *Graceful degradation.* The overview and the map are best-effort: if they
  fail, the analysis still completes without them. One malformed reply costs
  the files in it, not the run.

**Tradeoff.** Retries multiply quota use when a provider is genuinely
struggling. Bounded attempts and a hard deadline are what keep a bad hour
from becoming a drained quota.

## 11. Progressive delivery

**The idea.** When the slow part cannot be made fast, change what the user
waits *for*. Time to first useful result and time to completion are separate
numbers, and the first is the one people feel.

**Here.** Structure needs no model and is ready about 1.5 seconds after the
clone, so the pipeline publishes that partial graph immediately and
re-publishes as summaries, the overview and PR history land
(`progress.py`, `pipeline.run_pipeline`). The viewer opens on it and fills in
live, most-connected files first. Partial graphs are versioned and a poll
receives the graph only when its version changed, so polling every second
costs a few hundred bytes most of the time. A fixed list of stages with
counts, plus skeleton placeholders, tells the reader what is coming and how
far along it is.

**Tradeoff.** The UI must cope with data that is incomplete and changing
under it, which is a real source of bugs. Every view needs a "not yet" state.

## 12. Treating model output as untrusted

**The idea.** Text from a model is text from the internet. In this app it is
also influenced by the contents of arbitrary public repositories, which an
attacker can write.

**Here.**

- Model text is rendered by building React elements, never by setting
  `innerHTML` (`frontend/src/markdown.tsx`). Links the model writes are
  reduced to their label.
- The diagram is compiled by code from validated data, every label passes
  through one escaping function, and Mermaid runs in strict security mode
  with no click directives.
- Validation bounds what a manipulated answer could do: it cannot add a node
  that isn't a real file or exceed the caps.

**What is not defended.** Repository content goes into prompts verbatim, so
a file could contain text addressed to the model ("ignore your
instructions…"). The consequences are limited to bad summaries of that repo,
since the model has no tools and no access to anything else, but there is no
specific defence against it. That limited blast radius is itself the main
protection, and it is a direct result of this being a pipeline and not an
agent.

## 13. Provider abstraction

**The idea.** Models and providers change faster than applications. Keep the
dependency behind one seam.

**Here.** Everything reaches the model through `call_llm(prompt) -> str`.
Reasoning effort, temperature, token budget, streaming, timeouts, the
concurrency cap and error translation all live behind it, so changing
provider means rewriting one function body and no callers.

**Tradeoff.** The lowest-common-denominator interface (a string in, a string
out) is what rules out provider-specific features such as function calling,
constrained JSON and prompt caching.

## 14. Evaluation

**The idea.** You cannot improve what you do not measure, and model output
quality does not show up in unit tests.

**Here, what exists.** The deterministic parts are properly tested: 18 cases
asserting exact dependency edges per language
(`backend/tests/test_imports.py`). Model-facing decisions were made by
side-by-side comparison on real inputs: reasoning effort per task, the
concurrency level, the effect of prompt changes on the map.

**Here, what is missing.** There is no evaluation set and no automated
quality check on model output. Nothing would catch a prompt change that made
summaries worse, or a provider silently swapping the model. The honest
description of the current state is "measured once by hand, then trusted".

---

## Techniques deliberately not used

| Technique | What it is | Why not here | When it would earn its place |
|---|---|---|---|
| **RAG: embeddings, vector index, retrieval** | Split text into chunks, embed them, fetch the most similar ones per question | Questions are scoped by what the reader selected, so the relevant context is already known; the summaries act as a hand-built index | Free-form questions over a whole large repo ("where is retry logic handled?") where you can't know in advance which files matter |
| **Agents and tool use** | The model decides which actions to take, in a loop | The steps are known in advance, and a fixed pipeline is cheaper, faster, debuggable and has almost no attack surface | Open-ended investigation, such as tracing a bug across files by choosing what to read next |
| **Function calling / constrained JSON** | The provider guarantees output matches a schema | Kept `call_llm` provider-neutral | As soon as you commit to one provider; it would delete the salvage-parsing code |
| **Fine-tuning** | Train the model on your task | A general model with a good prompt is sufficient, and there is no labeled data | A high-volume narrow task where a small tuned model could replace a large one |
| **Prompt caching** | The provider reuses computation for a repeated prompt prefix | Not exposed by this provider | Every summary call repeats the same framing paragraph; caching it would cut input cost |
| **Semantic caching** | Reuse an answer for a *similar* earlier question | Only exact repeats are reused today | A busy deployment where many people ask near-identical questions |
| **Evals and LLM-as-judge** | An automated test suite for output quality | Not built yet (section 14) | Before the next prompt or model change. It is the most valuable missing piece |
| **Guardrails / moderation** | Filter inputs and outputs for unsafe content | Output is technical prose about public code, with no actions attached | Any user-generated input beyond a repo URL, or any tool access |

## Where each concept lives

| Concept | Open this |
|---|---|
| Code instead of a model | `backend/app/imports.py`, `symbols.extract_symbols`, `frontend/src/mermaid.ts` |
| Context engineering | `explain.describe_map`, `explain._focus_block`, `architecture._describe_graph` |
| Batching and budgets | `parser._plan_batches`, `parser.SUMMARY_TIERS` |
| Shared prompt framing | `backend/app/audience.py` |
| Structured output, validation, repair | `architecture.validate_architecture`, `generate_architecture` |
| Salvage parsing | `parser._parse_summaries` |
| Provenance and grounding | `models.Annotation`, `architecture._mark_backed` |
| Effort, temperature, token caps | `llm.call_llm` and each call site |
| Tiering, templates, lazy generation | `parser._templated_summary`, `main.ensure_symbol_explainers` |
| Memoization | `pipeline.load_cached`, `rationale_store.py` |
| Cheap recompute | `pipeline.refresh_dependencies`, `imports.RESOLVER_VERSION` |
| Concurrency cap | `llm._slots` |
| Streaming, timeouts, runaway guard, retries | `llm._stream_to_text`, `llm.call_with_retry`, `errors.py` |
| Progressive delivery | `progress.py`, `pipeline.run_pipeline`, `frontend/src/AnalysisProgress.tsx` |
| Untrusted output | `frontend/src/markdown.tsx`, `frontend/src/mermaid.ts` |

---

## Engineering decisions and outcomes

A record of the technical decisions behind this project and what each one
achieved, written so they can be lifted onto a resume and defended in an
interview. Each bullet is followed by what to be ready to say about it.

**On the numbers.** Every figure below was measured on this system unless
marked otherwise, but the measurements are small: single runs against one
repository (psf/requests: 121 files, 40 pull requests) on a free-tier model
endpoint. State them as "measured on a ~120-file repo", not as general
benchmarks. The pre-optimization end-to-end time was never measured
directly; it is *estimated* at 20+ minutes from per-call timings, so quote
the measured 594s → 204s instead.

### Resume bullets

**Latency and cost**

- Cut end-to-end repository analysis time by **66% (594s → 204s)** and
  model quota use by roughly **10x**, by profiling individual LLM calls and
  finding that hidden reasoning tokens, not input size, dominated cost
  (~9,000 characters of reasoning per 5,000 of output); tuned reasoning
  effort per task, reducing one stage from ~300s to 17s with no quality loss.
  - *Speak to:* why output tokens drive latency in autoregressive models;
    how you found it (timed one call, inspected the reasoning field) rather
    than guessing; and that you verified quality side by side, which is how
    you caught the one task where low effort made the output worse and kept
    it at full effort.
- Reduced **time-to-first-useful-result from minutes to 1.5 seconds** by
  restructuring a batch pipeline to publish partial results: static analysis
  completes first and renders immediately, while LLM-generated content
  streams into the UI as it lands, most-connected files first.
  - *Speak to:* decoupling time-to-first-view from time-to-completion; the
    versioned polling protocol that re-sends the payload only when it
    changed; and the cost, which is that every view needs a "not yet" state.
- Designed a **tiered generation strategy** that routes each file by value:
  full analysis for source code, one-line summaries for config and docs, and
  templated output with no model call for boilerplate, eliminating model
  calls for a large share of a typical repository.
  - *Speak to:* routing by expected value as a general cost pattern, and its
    failure mode (important logic living in a file type you deprioritized).
- Made expensive features **lazy and memoized**: per-function explanations
  and all Q&A are generated on first request and persisted, so each costs
  one model call for all users, ever, and a fresh analysis sends none.
  - *Speak to:* lazy evaluation plus caching applied to LLM features, and
    cache invalidation (you version the deterministic layer so it can be
    recomputed in ~2s with no model calls when the resolver improves).

**Reliability**

- Diagnosed and fixed a **production-class hang** in which silently dropped
  HTTP connections held every concurrency slot indefinitely; traced it to
  non-streaming requests idling for minutes, and resolved it by streaming
  responses with a stall timeout and bounded retries.
  - *Speak to:* how you found it (three ESTABLISHED sockets, zero progress,
    provider healthy on a fresh request); why idle connections get dropped by
    intermediaries; and the second-order bug, where your first timeout was
    shorter than the provider's queue wait and made runs slower by killing
    requests that were merely waiting their turn.
- Identified **runaway generation** (a model looping until its context was
  exhausted, consuming quota for 15+ minutes) and contained it with
  per-call token budgets enforced server-side, a client-side output ceiling,
  and a hard deadline, treating each as a retryable failure.
  - *Speak to:* why this is a sampling accident and not a prompt bug (the
    same prompt completed in 10s on retry), and defence in depth.
- Determined the optimal request concurrency **empirically**: measured that
  4 parallel requests yielded only ~1.3x throughput because the provider
  queued them, and set a process-wide semaphore accordingly.
  - *Speak to:* backpressure and rate-limited dependencies; why the limit is
    global rather than per job; why more concurrency can be slower.
- Built **fault-tolerant parsing** of model output that salvages complete
  entries from truncated or malformed JSON and re-requests only the missing
  items, eliminating a failure mode where one bad response discarded a
  whole batch.

**Correctness and trust**

- Enforced a **provenance rule** across the product: model inferences are
  never presented as fact. Author-stated and model-inferred rationale are
  separate fields with confidence levels, and every diagram edge is verified
  against the static import graph and drawn differently when it is only the
  model's claim.
  - *Speak to:* hallucination as the core risk in LLM products; grounding
    against deterministic data; why an honest gap beats a confident guess.
- Constrained an LLM to emit a **validated JSON AST rather than diagram
  syntax**, with a deterministic compiler producing the output, which
  guarantees every node maps to a real file and removes a class of
  malformed-output and injection problems.
  - *Speak to:* keeping the model away from anything code can do; treating
    model output as untrusted input; the validate-and-repair loop and why
    repair is reserved for genuinely unusable results.
- Built a **static dependency resolver covering 11 language families**
  (Python, JS/TS, Go, Rust, JVM, C/C++, Ruby, PHP, Dart, CSS, HTML),
  including tsconfig path aliases, monorepo workspaces, Go modules and Rust
  crate paths, with **18 tests asserting exact edge sets**.
  - *Speak to:* why regex plus per-language resolution beat shipping a
    parser toolchain per language; why tests assert exact sets (an extra
    edge is as wrong as a missing one); the documented blind spots.

**Product and frontend**

- Designed an **interactive architecture map** that groups a repository's
  real files into semantic layers with labeled data flows, rendered with
  Mermaid and an ELK layout engine on a custom pan/zoom canvas, with
  click-through to source and context-aware Q&A scoped to the selection.
  - *Speak to:* why you switched layout engines (the default laid out
    subgraphs as a diagonal staircase); choosing the layout direction by
    measuring which fits the viewport; and the input-handling decision to
    make the wheel always zoom, because smooth-scrolling mice and trackpads
    send indistinguishable events.
- Reduced interaction depth by consolidating a multi-step drill-down into a
  single source view with tabbed context, and replaced spinners with
  content-shaped **skeleton states** across every asynchronous surface.

### Skills this project demonstrates

LLM application architecture · prompt and context engineering · structured
output and validation · latency and cost optimization · grounding and
hallucination mitigation · resilience engineering for remote dependencies
(timeouts, retries, backpressure) · static analysis · async job design and
progressive delivery · React and TypeScript · FastAPI and Python ·
data visualization · measurement-driven decision making

### How to talk about it honestly

This project was built with heavy AI assistance, which is worth saying
plainly rather than leaving for an interviewer to discover; directing an AI
collaborator well is a skill employers now look for. What you own is the
product direction (every feature above began as your requirement), the
judgment calls, and the understanding. The bullets are only as strong as
your ability to explain the reasoning behind each one, so use the
"speak to" notes and the sections above as a study guide: if you can
explain *why* the stall timeout had to exceed the queue wait, the bullet is
yours.
