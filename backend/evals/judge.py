"""A model as judge, for the quality code can't score.

Length and coverage can be computed. Whether a summary is *accurate to the
code* and *explains why rather than what* cannot, so a stronger model is
asked to compare two variants' summaries of the same file.

Three things keep a judge honest, and all three are built in:
- **Pairwise, not absolute.** "Which is better?" is a question models answer
  far more consistently than "rate this 1-5".
- **Randomized order.** Judges favour whichever answer comes first. Each
  pair is shown in a random order, and the report includes how often
  position A won, which should sit near 50%: if it doesn't, the verdicts
  are measuring position, not quality.
- **A different, stronger model** than the one being judged, since a model
  tends to prefer its own style.

A judge is still a model. Before relying on it, rate a handful of pairs
yourself and check it agrees with you.
"""

import json
import os
import random

from app.llm import call_llm, call_with_retry

JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "gpt-oss:120b")

_RUBRIC = """You are judging two summaries of the same source file, written for a
software engineer who is reading an unfamiliar codebase to learn from it.

Prefer the summary that is better on these, in this order:
1. Accurate: every claim is supported by the file. A confident claim the file
   does not support is the worst fault and should lose on its own.
2. Explains design: says why the file is shaped this way and what it costs,
   not just what it contains.
3. Specific: names the real identifiers and mechanisms, not generalities.
4. Efficient: no filler, no restating the file name, no padding.

Length is not quality. Do not prefer a summary for being longer.

File: {path}
```
{content}
```

Summary A:
{a}

Summary B:
{b}

Return ONLY a JSON object: {{"winner": "A" | "B" | "tie", "reason": "one sentence"}}
"""


def judge_summaries(case: dict, left: dict, right: dict, sample: int = 6, seed: int = 0) -> dict:
    rng = random.Random(seed)
    by_path = {item["path"]: item for item in case["summaries"]}
    shared = [p for p in left if p in right and p in by_path and left[p] and right[p]]
    rng.shuffle(shared)
    verdicts = []
    for path in shared[:sample]:
        left_first = rng.random() < 0.5
        a, b = (left[path], right[path]) if left_first else (right[path], left[path])
        prompt = _RUBRIC.format(path=path, content=by_path[path]["content"][:3000], a=a, b=b)
        raw = call_with_retry(lambda: call_llm(prompt, model=JUDGE_MODEL, think="low", temperature=0, max_tokens=1200))
        try:
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            pick = str(data.get("winner", "")).strip().upper()
        except (json.JSONDecodeError, ValueError):
            continue
        winner = "tie" if pick not in ("A", "B") else ("left" if (pick == "A") == left_first else "right")
        verdicts.append({"path": path, "winner": winner, "position": pick if pick in ("A", "B") else None, "reason": data.get("reason")})
    decided = [v for v in verdicts if v["winner"] != "tie"]
    positions = [v["position"] for v in verdicts if v["position"]]
    return {
        "judge_model": JUDGE_MODEL,
        "pairs": len(verdicts),
        "left_wins": sum(v["winner"] == "left" for v in verdicts),
        "right_wins": sum(v["winner"] == "right" for v in verdicts),
        "ties": len(verdicts) - len(decided),
        # Should be near 0.5. Far from it, the judge is rewarding position.
        "position_a_win_rate": round(positions.count("A") / len(positions), 2) if positions else None,
        "verdicts": verdicts,
    }
