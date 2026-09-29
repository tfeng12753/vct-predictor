"""Render experiment results as a Markdown table (used for the CI run summary).

    python -m pipeline.report site/data/experiments.json [keys...]
"""
from __future__ import annotations

import json
import sys


def fmt(w):
    return f"{w['delta'] * 1000:+.2f} [{w['lo'] * 1000:+.2f}, {w['hi'] * 1000:+.2f}] (n={w['n']})"


def render(exps, keys=()):
    out = ["Change in series log loss ×1000 versus the current model (negative is better), 95% paired bootstrap interval.", ""]
    for e in exps:
        if keys and e["key"] not in keys:
            continue
        out += [f"### {e['title']}", "", f"_{e['hypothesis']}_", ""]
        if not e.get("rows"):
            out += [e.get("note", "No results."), ""]
            continue
        out += ["| Setting | Validation | Holdout | Decision |", "|---|---|---|---|"]
        out += [f"| {r['setting']} | {fmt(r['validation'])} | {fmt(r['holdout'])} | {r['decision']} |" for r in e["rows"]]
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    path, *keys = sys.argv[1:]
    print(render(json.load(open(path))["experiments"], set(keys)))
