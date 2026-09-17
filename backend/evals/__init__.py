"""Evaluation harness: measuring model output quality, repeatably.

A unit test asks "is this function correct?". An eval asks "is this model
output good?", which has no single right answer, so it is measured rather
than asserted. The harness is four parts:

  cases.py    Frozen inputs. The exact files and pull requests a model call
              sees, saved to disk, so two runs differ only in what was
              changed on purpose.
  suites.py   One suite per model call site (file summaries, PR rationale,
              the architecture map). A suite runs the production prompt over
              the frozen inputs under a named *variant* — reasoning effort,
              temperature — and returns the outputs.
  checks.py   Scorers: pure functions turning outputs into numbers, plus the
              thresholds that decide pass or fail.
  judge.py    A stronger model comparing two variants' outputs pairwise, for
              the quality that code can't score.

Run from backend/:

  python -m evals freeze psf/requests      # save inputs (no model calls)
  python -m evals check-graph <graph.json> # score a finished analysis (free)
  python -m evals run prs --variant low:think=low --variant default
  python -m evals judge summaries low default --sample 6

Suites evaluate a *call site* with fixed inputs, not the whole pipeline end
to end: that is what makes a run cheap (a handful of calls), fast, and
attributable — when a number moves, there is one prompt it can be blamed on.
"""
