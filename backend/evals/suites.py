"""One suite per model call site.

A suite takes the frozen inputs and a *variant* — the knobs under test — and
returns raw outputs plus timing. It builds its prompt with the production
prompt function and parses with the production parser, so what is measured is
what ships; only the knobs passed to `call_llm` differ. To evaluate a prompt
*wording* change, edit the prompt and compare against the saved baseline.
"""

import json
import time
from dataclasses import dataclass, field

from app.llm import call_llm, call_with_retry


@dataclass
class Variant:
    name: str
    think: str | None = "__prod__"
    temperature: float | None = None
    model: str | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, spec: str) -> "Variant":
        # "low:think=low,temperature=0.2"  |  "default"  |  "prod"
        name, _, rest = spec.partition(":")
        variant = cls(name=name)
        if name == "default" and not rest:
            variant.think = None  # the model's own default effort
        for pair in filter(None, rest.split(",")):
            key, _, value = pair.partition("=")
            if key == "think":
                variant.think = None if value in ("default", "none") else value
            elif key == "temperature":
                variant.temperature = float(value)
            elif key == "model":
                variant.model = value
            else:
                raise SystemExit(f"Unknown variant setting {key!r} (use think, temperature, model)")
        return variant

    def kwargs(self, prod_think: str | None, prod_temperature: float | None = None) -> dict:
        kw: dict = {"think": prod_think if self.think == "__prod__" else self.think}
        temperature = self.temperature if self.temperature is not None else prod_temperature
        if temperature is not None:
            kw["temperature"] = temperature
        if self.model:
            kw["model"] = self.model
        return kw


def _timed(fn):
    started = time.time()
    value = fn()
    return value, round(time.time() - started, 1)


def run_summaries(case: dict, variant: Variant, sample: int | None = None) -> dict:
    from app.parser import _parse_summaries, _plan_batches, _summary_prompt

    inputs = case["summaries"]
    if sample:
        half = max(1, sample // 2)
        inputs = [i for i in inputs if i["tier"] == "source"][:half] + [i for i in inputs if i["tier"] == "brief"][:half]
    outputs: dict[str, str] = {}
    calls = seconds = parse_failures = 0
    for tier in ("source", "brief"):
        entries = [(i["path"], i["content"], i["dependencies"]) for i in inputs if i["tier"] == tier]
        for batch in _plan_batches(entries, tier):
            prompt = _summary_prompt(batch, tier)
            budget = 600 + len(batch) * (700 if tier == "source" else 220)
            raw, took = _timed(lambda: call_with_retry(lambda: call_llm(prompt, max_tokens=budget, **variant.kwargs("low"))))
            calls += 1
            seconds += took
            parsed = _parse_summaries(raw)
            if not parsed:
                parse_failures += 1
            outputs.update(parsed or {})
    return {"inputs": inputs, "outputs": outputs, "calls": calls, "seconds": round(seconds, 1), "parse_failures": parse_failures}


def run_prs(case: dict, variant: Variant, sample: int | None = None) -> dict:
    from app.miner import EFFORT, RATIONALE_BATCH_SIZE, _extraction_prompt, parse_extractions

    inputs = case["prs"][:sample] if sample else case["prs"]
    outputs: list[dict] = []
    calls = seconds = parse_failures = 0
    for i in range(0, len(inputs), RATIONALE_BATCH_SIZE):
        batch = inputs[i : i + RATIONALE_BATCH_SIZE]
        prompt = _extraction_prompt(batch)
        # Roomy enough for the default-effort variant's reasoning tokens too,
        # so a comparison isn't decided by one side hitting the cap.
        budget = 3500 + len(batch) * 500
        raw, took = _timed(lambda: call_with_retry(lambda: call_llm(prompt, max_tokens=budget, **variant.kwargs(EFFORT))))
        calls += 1
        seconds += took
        parsed = parse_extractions(raw)
        if len(parsed) < len(batch):
            parse_failures += 1
        outputs.extend(parsed)
    return {"inputs": inputs, "outputs": outputs, "calls": calls, "seconds": round(seconds, 1), "parse_failures": parse_failures}


def run_map(case: dict, variant: Variant, sample: int | None = None) -> dict:
    from app import architecture as A

    graph = case["graph"]
    if graph is None:
        raise SystemExit("This case has no graph.json: analyze the repo once, then re-run `freeze`.")
    owner, _, name = case["meta"]["repo"].partition("/")
    prompt = A._prompt(owner, name, graph.get("overview") or "", A._describe_graph(graph["nodes"]), None)
    raw_text, took = _timed(lambda: call_with_retry(lambda: call_llm(prompt, max_tokens=5000, **variant.kwargs(A.EFFORT, A.TEMPERATURE))))
    try:
        raw = A._parse_json_object(raw_text)
        validated, problems = A.validate_architecture(raw, graph["nodes"])
    except (json.JSONDecodeError, TypeError, ValueError):
        raw, validated, problems = None, None, ["response was not valid JSON"]
    return {"raw": raw, "validated": validated, "problems": problems, "calls": 1, "seconds": took, "parse_failures": int(raw is None)}


SUITES = {"summaries": run_summaries, "prs": run_prs, "map": run_map}
