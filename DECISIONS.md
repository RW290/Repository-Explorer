# Decision log

Every significant engineering decision in this project: what was decided,
why, what else was on the table, what evidence there was, what it costs, and
how to challenge it. It exists so the reasoning can be learned from and
questioned, not just inherited.

This is the one document here that keeps history on purpose. The README and
the code comments describe the system as it is; this records how it got that
way, including the calls that were wrong.

**How to read an entry.** *Evidence* says whether the decision rests on a
measurement, a single observation, or judgment, because those deserve
different amounts of trust. *Question it* names what would change the call.
If you think one of those conditions holds, that's the argument to make.

**How to add to it.** New decisions get the next number. A decision that
gets overturned keeps its entry and gains an *Overturned by* line; the
mistake and what exposed it are usually the most useful part.

---

## Questions asked

Questions raised along the way, with the answers given. Each points at the
decisions it bears on.

**Q1. What does "Method Not Allowed" mean when I analyze a repo?**
The frontend was newer than the backend process serving it. It called an
endpoint the old process didn't have, the request fell through to a catch-all
route that only accepts GET, and that route answered 405. Nothing was wrong
with the repo. Restarting the backend fixed it. → D-09.

**Q2. The token saving dropped a request from ~5% of my quota to ~0.5%. Is
that real?** Yes. Cost tracks tokens *written*. Three changes cut that hard:
hidden reasoning turned down where it added nothing, one-sentence summaries
for the ~70% of files that aren't source, and a token cap that stops runaway
generations, any one of which could cost more than a healthy analysis.
→ D-14, D-15, D-19.

**Q3. Does this use AI engineering concepts like chunking?**
Partly. It does *batching* and *budgeted context packing* (group by size
budget, take the head of each file, order by importance). Chunking proper
means overlapping pieces, embeddings and similarity retrieval, and there is
none of that here. → D-15, and `AI_ENGINEERING.md` §3.

**Q4. Shouldn't function explainers only be generated once a file is opened,
so there's less immediate work?** That is already the design: the analysis
pipeline never generates them. The source viewer requests them when a file
opens, and the result is stored for everyone after. → D-08.

**Q5. How would I implement evals, embeddings and RAG, and is this the right
project for it?** Evals: yes, here, and first, because you need them to know
whether anything else works. RAG: a closer call (code is a harder retrieval
domain than docs), but one deep project beats two shallow ones. → D-23
onward.

**Q6. What does "building an eval harness" actually mean?**
A unit test asks whether a function is correct; an eval asks whether model
output is *good*, which has no single right answer, so you measure it. The
harness is frozen inputs, named variants, scorers, and a report with gates.
→ D-23 to D-28.

---

## Decisions

### Architecture map

**D-01. The model returns a JSON description of the map; code draws it.**
*Why:* a model writing diagram syntax directly can produce malformed output
or smuggle in markup, and neither is checkable. JSON can be validated against
the real file list before anything is drawn.
*Alternatives rejected:* have the model emit Mermaid (what the inspiration,
gitdiagram, originally did). *Evidence:* judgment, plus gitdiagram's own move
to the same split. *Cost:* a compiler to maintain. *Question it:* if a
provider's constrained-output mode guaranteed valid diagrams, the compiler
would still be needed for validation against real paths, so probably never.

**D-02. Every edge is checked against the import graph and drawn solid or
dashed accordingly.**
*Why:* it extends the project's core rule (stated vs inferred) to the map, so
the reader can see which flows the code proves and which the model merely
claims. *Alternatives rejected:* trust all model edges; or drop unverified
ones (loses real runtime relationships like HTTP calls that imports can't
show). *Evidence:* judgment. *Cost:* dashed edges look less authoritative in
a demo. *Question it:* if users ignore the distinction, it is noise.

**D-03. ELK for layout instead of Mermaid's default (dagre).**
*Why:* dagre laid subgraphs out as a diagonal staircase once edges crossed
group boundaries, which is all an architecture map is. *Evidence:* observed
in screenshots of the same graph under both. *Cost:* a second dependency,
pinned to the release matching Mermaid 11.

**D-04. Render both layout directions and keep the one that fits better.**
*Why:* a five-layer pipeline is a long strip one way and a tall column the
other, and which is worse depends on the screen. *Alternatives rejected:*
always left-to-right (my first choice; at fit it was an unreadable strip).
*Evidence:* screenshots. *Cost:* two renders, a couple of hundred
milliseconds. *Question it:* on very large maps the double render may matter.

**D-05. Map nodes are the repository's real files and folders, not
model-invented "components". — *your call, overruling mine.***
*Why:* you wanted the boxes to be the same nodes as the explorer. It also
turned out to be the more honest design: every box is something you can open,
and a path that doesn't resolve is dropped. *What I had built:* invented
components each "pointing at" a file. *Cost:* systems outside the repo
couldn't be boxes, which led to D-06.

**D-06. External systems are allowed as a distinct, greyed node kind.**
*Why:* D-05 made things like urllib3 undrawable, and they matter to
understanding. Namespacing them `ext:` means they can never collide with a
real path, and any external nothing connects to is dropped.
*Cost:* they are entirely the model's claim, with no file to check against.

**D-07. Low sampling temperature (0.2) and a tightened prompt for the map.**
*Why:* one regeneration produced 40 edges, 39 labelled "imports". *Evidence:*
a single bad run against a single good one, so weak; the prompt now forbids
per-import edges and empty labels, and the validator strips them regardless.
*Question it:* the eval's `empty_edge_labels` metric (D-25) is now the real
guard; the temperature setting is unproven on its own.

### Features

**D-08. Function explainers are generated on first open, then stored.**
*Why:* most files are never opened, so generating explainers during analysis
would spend quota on work nobody sees. Storing them means each file costs one
generation, ever, for every visitor. *Alternatives rejected:* generate during
analysis (simpler, far more expensive). *Cost:* the first person to open a
large file waits tens of seconds. *Question it:* if most users open most
files, precomputing in the background after analysis would be better.

**D-09. A stale backend explains itself instead of saying "Method Not
Allowed", and the dev backend auto-reloads.**
*Why:* the error was accurate and useless (Q1). Auto-reload removes the
cause in development. *Cost:* none worth naming.

**D-10. Clicking a file opens its source directly; details moved into the
source view's side bar. — *your call.***
*Why:* map → panel → "view source" was three steps to reach the thing people
actually want. *Cost:* the side bar now carries a lot, hence tabs. Folders
and externals keep the panel because they have no source.

**D-11. The wheel always zooms; dragging moves. — *your call, overruling
mine.***
*What I had built:* a heuristic that treated small scroll deltas as a
trackpad and panned. *Why it was wrong:* smooth-scrolling mice send the same
small deltas, so your wheel was being misread. The two are genuinely
indistinguishable from the events. *Lesson:* don't infer hardware from event
shape when a simpler rule serves everyone.

**D-12. Skeleton placeholders instead of spinners. — *your call.***
*Why:* a skeleton shows what is coming and where, and prevents layout jumps.
*Detail:* the source viewer's loading fallback needed its own stylesheet,
because the viewer's styles ship inside the very chunk being waited for.

**D-13. An Ask panel for the architecture, scoped to the selection.**
*Why:* "explain this section" needs a referent, so group boxes became
clickable and the selection is sent as a focus block. The model is handed the
map as text so answers are about this diagram. *Evidence:* your REST-vs-POST
example was answered by untangling the category error, which the prompt asks
for explicitly. *Cost:* one model call per question, shared afterwards.

### Speed, cost and reliability

**D-14. Low reasoning effort for summaries, the overview, the map,
explainers and interactive answers.**
*Why:* measured. At default effort the model wrote ~9,000 characters of
hidden reasoning to produce ~5,000 of summaries (50s); at low, ~50 (27s).
The map went from ~300s to 17s. *Evidence:* single timed runs per setting,
plus reading the outputs. *Cost:* a quality risk that is invisible without
comparison, which is what D-23 onward addresses. *See also D-26.*

**D-15. Tier files by value: paragraph for source, a sentence for docs and
config, a template and no model call for boilerplate.**
*Why:* output tokens are the cost, and most of a repo isn't source.
*Alternatives rejected:* summarize everything equally (the original).
*Cost:* important logic in a deprioritized file type gets a thin summary.
*Question it:* if `.yaml` workflows turn out to matter to readers, promote
them a tier.

**D-16. Publish the graph as soon as structure is known; fill in live.**
*Why:* clone plus import resolution takes ~1.5s and needs no model, so the
reader can explore a real graph while the slow part runs. Time to first view
and time to completion are separate numbers, and the first is the one people
feel. *Cost:* every view needs a "not yet" state, a real source of bugs.

**D-17. Concurrency of 2, as one process-wide limit.**
*Why:* measured: four concurrent calls took ~122s against ~160s in sequence,
so the provider mostly queues. *Overturned an earlier value:* I first set 3;
see D-18. *Question it:* a paid tier with real parallelism would want more,
which is why it's an environment variable.

**D-18. Stream responses, with a 240-second stall timeout.**
*Why:* non-streaming calls left the socket silent for minutes; three were
silently dropped and, with no timeout, held every call slot forever.
*A mistake on the way:* my first timeout was 90s, shorter than the provider's
own queue wait, so it killed requests that were merely waiting and sent each
retry to the back of the queue. A run got *slower* (summaries 440s vs 259s).
*Lesson:* a timeout must exceed the longest legitimate wait, including
queueing you cause yourself.

**D-19. A token budget on every call.**
*Why:* the model occasionally loops until its context is full; two calls
generated for fifteen minutes. The same prompt finished in 10s on retry, so
it's a sampling accident, and the defence is a fuse: a provider-enforced cap,
a client-side ceiling, a deadline, all retryable. *Cost:* a cap set too low
truncates good answers, which is why truncated replies are salvaged.

**D-20. Tolerant parsing that salvages complete entries.**
*Why:* one malformed reply was discarding a whole batch ("Summary
unavailable" on six files at once in an early cache). *Extended by D-27* to
PR rationale, which had kept the same bug.

### Dependency resolution

**D-21. Regex plus per-language resolution rules, not a parser per language.**
*Why:* import statements are the most regular syntax any language has, and a
real parser per language means a native toolchain per language on the server.
The hard part is resolution (path aliases, module paths, crate paths), which
is language semantics either way. *Evidence:* no edges lost on psf/requests
against the old Python-only resolver; 18 tests assert exact edge sets.
*Cost:* unusual import syntax is missed, as missing edges rather than wrong
ones. *Question it:* a language where imports aren't line-regular.

**D-22. Version the resolver; refresh old graphs' edges without the model.**
*Why:* improving the resolver shouldn't invalidate the expensive,
model-written parts of an analysis. Re-deriving edges takes ~2s and no quota.

### Evaluation harness

**D-23. Evaluate call sites with frozen inputs, not the pipeline end to end.**
*Why:* a component eval is cheap (a handful of calls), fast, and
attributable: when a number moves, there is one prompt to blame. An
end-to-end eval costs a full analysis per variant and can't say which stage
changed. *Alternatives rejected:* end-to-end runs; evaluating only finished
cached graphs (free, but can't compare variants). *Cost:* it won't catch
problems that only appear when stages interact.

**D-24. Freeze inputs to disk and commit them.**
*Why:* an eval whose inputs drift (a new commit, an edited PR) can't tell a
prompt change from a data change. *Cost:* ~120KB of a third-party repo's text
in this one; one repository is a thin sample.
*Question it:* results here describe psf/requests. More cases are needed
before trusting a conclusion broadly.

**D-25. Choose metrics from failures that actually happened.**
*Why:* each scorer names the incident behind it: `coverage` (a bad reply
blanking a batch), `empty_edge_labels` (the 39-of-40 "imports" map),
`members_resolved` (invented paths), `verbatim_overlap` (quoting the author),
the both-rationales check (the integrity rule). Measuring what has already
gone wrong beats guessing at what might.

**D-26. PR rationale moves to low reasoning effort. — *overturns part of
D-14.***
*What I originally decided:* keep PR rationale at default effort, because in
a hand comparison low effort quoted the author verbatim and default restated.
*What the eval showed:* on the first run, the reverse: default copied
(overlap 0.77, three PRs at 1.00), low restated (0.14). So copying was
**sampling noise at either setting**, and I had mistaken one sample for a
pattern. After D-27 both score under 0.03 and read the same side by side, and
low is ~25% faster on the longest stage of an analysis.
*Lesson:* this is the exact error evals exist to prevent. One observation per
setting is an anecdote.
*Question it:* it still rests on one repo and one run per variant.

**D-27. Fix quoting in the prompt, not with a model setting.**
*Why:* the prompt asked for an account "faithful to what they actually said",
which invites copying. It now asks for the reason "restated… never copied".
*Evidence:* measured: mean verbatim overlap 0.77 → 0.005. This is the
harness paying for itself on its first use.

**D-28. Pairwise model judging, randomized order, a stronger model, and a
reported position bias.**
*Why:* "which is better" is answered more consistently than "rate 1 to 5";
judges favour the first answer, so order is randomized and the position-A
win rate is printed. *Result so far:* over 4 files, default 2, low 1, 1 tie,
position-A 67%. **Inconclusive**: four pairs decides nothing, and that
position rate is itself a warning.
*The useful finding:* the judge's reasons flagged **unsupported claims in
summaries at both efforts**, which no code check can see.

**D-29. Gates are absolute floors; the baseline is for spotting drift.**
*Why:* a gate is a floor under behaviour the product depends on and should
rarely change; a saved baseline shows when a number moved while still
passing. *Cost:* thresholds were chosen by judgment from one repo's numbers.

**D-30. A provider outage is reported, not scored.**
*Why:* the map suite failed on "temporarily overloaded". Recording that as a
quality failure would blame the model for the network.

---

## Open problems the log points at

- **Summary accuracy** (from D-28). Likely cause: files are truncated to
  3,000 characters and the model speculates about the rest. Next step: a
  judged sample large enough to mean something, and a faithfulness check.
- **One eval case.** Every number here is psf/requests. Add two or three
  repos of different shapes before trusting a conclusion broadly.
- **No hand labels yet.** `evals/cases/psf__requests/labels.json` is an empty
  template; filling it in enables accuracy against your own judgment.
- **Judge not calibrated.** Rate ~20 pairs yourself and compare before
  leaning on it.
