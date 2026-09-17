"""Command line for the eval harness. See evals/__init__.py for the idea."""

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
BASELINE = HERE / "baseline.json"
load_dotenv(HERE.parent.parent / ".env")

from app.errors import PipelineError  # noqa: E402
from evals import cases, checks  # noqa: E402  (after load_dotenv: app.llm reads the env at import)
from evals.suites import SUITES, Variant  # noqa: E402

DEFAULT_CASE = "psf/requests"


def _score(suite: str, case: dict, result: dict) -> dict:
    if suite == "summaries":
        metrics = checks.score_summaries(result["inputs"], result["outputs"])
    elif suite == "prs":
        metrics = checks.score_prs(result["inputs"], result["outputs"], case["labels"])
    else:
        metrics = checks.score_map(result["raw"], result["validated"], result["problems"], case["labels"])
    metrics["calls"] = result["calls"]
    metrics["seconds"] = result["seconds"]
    metrics["parse_failures"] = result["parse_failures"]
    return metrics


def _table(rows: dict[str, dict], baseline: dict | None = None) -> str:
    names = list(rows)
    keys = list(dict.fromkeys(k for metrics in rows.values() for k in metrics))
    width = max(len(k) for k in keys) + 2
    lines = ["".ljust(width) + "".join(n.rjust(14) for n in names) + ("baseline".rjust(14) if baseline else "")]
    for key in keys:
        cells = []
        for name in names:
            value = rows[name].get(key)
            cells.append(("-" if value is None else f"{value:.3g}" if isinstance(value, float) else str(value)).rjust(14))
        if baseline:
            b = baseline.get(key)
            cells.append(("-" if b is None else f"{b:.3g}" if isinstance(b, float) else str(b)).rjust(14))
        lines.append(key.ljust(width) + "".join(cells))
    return "\n".join(lines)


def cmd_freeze(args) -> int:
    owner, _, name = args.repo.partition("/")
    out = cases.freeze(owner, name, args.files // 2, args.files - args.files // 2, args.prs)
    meta = json.loads((out / "meta.json").read_text())
    print(f"Froze {meta['repo']} @ {meta['commit'][:10]}: {meta['files']} files, {meta['prs']} PRs -> {out}")
    print(f"Optional: fill in {out / 'labels.json'} to score against your own judgment.")
    return 0


def cmd_check_graph(args) -> int:
    metrics = checks.score_graph(json.loads(Path(args.graph).read_text()))
    print(_table({"graph": metrics}))
    failures = checks.gate("graph", metrics)
    print("\nGATE:", "pass" if not failures else "FAIL\n  - " + "\n  - ".join(failures))
    return 1 if failures else 0


def cmd_run(args) -> int:
    owner, _, name = args.case.partition("/")
    case = cases.load(owner, name)
    variants = [Variant.parse(v) for v in (args.variant or ["prod"])]
    baseline_all = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    baseline = baseline_all.get(args.suite, {}).get(args.case)

    rows, saved, failed, errored = {}, {}, False, []
    for variant in variants:
        print(f"running {args.suite} / {variant.name} on {args.case} …", file=sys.stderr)
        try:
            result = SUITES[args.suite](case, variant, args.sample)
        except PipelineError as e:
            # A provider outage is not a quality result. Say so and move on,
            # rather than recording a failure the model didn't earn.
            print(f"  could not run {variant.name}: {e}", file=sys.stderr)
            errored.append(variant.name)
            continue
        rows[variant.name] = _score(args.suite, case, result)
        saved[variant.name] = {"metrics": rows[variant.name], **{k: v for k, v in result.items() if k != "inputs"}}

    if not rows:
        print(f"\nNo variant of {args.suite} could be run ({', '.join(errored)}): the model backend was unavailable. Nothing was scored.")
        return 2
    print(f"\n{args.suite} on {args.case} @ {case['meta']['commit'][:10]} (frozen {case['meta']['frozen_at']})\n")
    print(_table(rows, baseline))
    for name, metrics in rows.items():
        failures = checks.gate(args.suite, metrics)
        failed |= bool(failures)
        print(f"\nGATE {name}:", "pass" if not failures else "FAIL\n  - " + "\n  - ".join(failures))

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}__{args.suite}.json"
    out.write_text(json.dumps({"suite": args.suite, "case": args.case, "commit": case["meta"]["commit"], "variants": saved}, indent=1))
    print(f"\nsaved {out.relative_to(HERE.parent)}")

    if args.accept:
        chosen = rows[variants[0].name]
        baseline_all.setdefault(args.suite, {})[args.case] = chosen
        BASELINE.write_text(json.dumps(baseline_all, indent=2))
        print(f"baseline for {args.suite}/{args.case} set from variant {variants[0].name!r}")
    return 1 if failed else 0


def cmd_accept(args) -> int:
    data = json.loads(Path(args.results).read_text())
    if args.variant not in data["variants"]:
        raise SystemExit(f"{args.results} has variants {sorted(data['variants'])}, not {args.variant!r}")
    baseline_all = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    baseline_all.setdefault(data["suite"], {})[data["case"]] = data["variants"][args.variant]["metrics"]
    BASELINE.write_text(json.dumps(baseline_all, indent=2))
    print(f"baseline for {data['suite']}/{data['case']} set from {args.variant!r} in {Path(args.results).name}")
    return 0


def cmd_judge(args) -> int:
    from evals.judge import judge_summaries

    owner, _, name = args.case.partition("/")
    case = cases.load(owner, name)
    files = sorted(RESULTS_DIR.glob("*__summaries.json"))
    outputs: dict[str, dict] = {}
    for path in reversed(files):  # newest first
        for variant, data in json.loads(path.read_text())["variants"].items():
            outputs.setdefault(variant, data["outputs"])
    missing = [v for v in (args.left, args.right) if v not in outputs]
    if missing:
        raise SystemExit(f"No saved summaries run has variant(s) {missing}. Run `python -m evals run summaries --variant …` first.")
    report = judge_summaries(case, outputs[args.left], outputs[args.right], args.sample, args.seed)
    print(f"\njudge {report['judge_model']}: {args.left} vs {args.right} over {report['pairs']} files")
    print(f"  {args.left}: {report['left_wins']}   {args.right}: {report['right_wins']}   ties: {report['ties']}")
    print(f"  position-A win rate: {report['position_a_win_rate']} (near 0.5 means the order isn't deciding it)")
    for v in report["verdicts"]:
        winner = {"left": args.left, "right": args.right, "tie": "tie"}[v["winner"]]
        print(f"  - {v['path']}: {winner} — {v['reason']}")
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}__judge.json").write_text(json.dumps({"left": args.left, "right": args.right, **report}, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("freeze", help="save a repo's inputs as an eval case (no model calls)")
    p.add_argument("repo", help="owner/name")
    p.add_argument("--files", type=int, default=16)
    p.add_argument("--prs", type=int, default=10)
    p.set_defaults(fn=cmd_freeze)

    p = sub.add_parser("check-graph", help="score a finished analysis JSON (no model calls)")
    p.add_argument("graph")
    p.set_defaults(fn=cmd_check_graph)

    p = sub.add_parser("run", help="run a suite under one or more variants, score, gate, save")
    p.add_argument("suite", choices=sorted(SUITES))
    p.add_argument("--case", default=DEFAULT_CASE)
    p.add_argument("--variant", action="append", help='e.g. "prod", "default", "low:think=low", "cold:temperature=0"')
    p.add_argument("--sample", type=int, help="use only the first N inputs, to spend fewer calls")
    p.add_argument("--accept", action="store_true", help="record the first variant's metrics as the baseline")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("accept", help="record a saved run's variant as the baseline (no model calls)")
    p.add_argument("results")
    p.add_argument("variant")
    p.set_defaults(fn=cmd_accept)

    p = sub.add_parser("judge", help="a stronger model compares two variants' saved summaries")
    p.add_argument("suite", choices=["summaries"])
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("--case", default=DEFAULT_CASE)
    p.add_argument("--sample", type=int, default=6)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_judge)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
