"""Execution accuracy on the gold set.

    python -m copilot.benchmark                       # local engine, gold set
    python -m copilot.benchmark --set dev             # what I iterated on
    python -m copilot.benchmark --engine cortex       # Cortex Analyst + Snowflake (needs creds)

A question passes when the generated SQL, after the guardrails, returns the
same rows as the gold SQL. Matching rules:
  * row order is ignored,
  * every gold column has to appear in the answer (matched by values, not by
    name). Extra columns are fine: "failed trades by counterparty" can come
    back with a count and a notional,
  * numbers are compared rounded to 2 decimals.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import statistics

import yaml

from .config import ROOT
from .executor import LocalExecutor, Result
from .pipeline import Copilot


def _norm(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    try:
        return round(float(v), 2)  # Decimal and numeric strings from Snowflake
    except (TypeError, ValueError):
        return str(v)


def _key(v):
    return (0, "") if v is None else ((1, v) if isinstance(v, float) else (2, str(v)))


def results_match(gold: Result, got_columns, got_rows) -> bool:
    if gold.error or got_rows is None:
        return False
    if len(gold.rows) != len(got_rows):
        return False
    if not gold.rows:
        return True
    g_cols = [[_norm(r[i]) for r in gold.rows] for i in range(len(gold.columns))]
    p_cols = [[_norm(r[j]) for r in got_rows] for j in range(len(got_columns))]
    cands = []
    for gc in g_cols:
        gs = sorted(gc, key=_key)
        cands.append([j for j, pc in enumerate(p_cols) if sorted(pc, key=_key) == gs])
        if not cands[-1]:
            return False
    gold_rows = sorted(zip(*g_cols), key=lambda r: [_key(v) for v in r])
    for mapping in itertools.product(*cands):
        if len(set(mapping)) != len(mapping):
            continue
        got = sorted((tuple(p_cols[j][k] for j in mapping) for k in range(len(got_rows))),
                     key=lambda r: [_key(v) for v in r])
        if got == gold_rows:
            return True
    return False


def load_set(name: str) -> list[dict]:
    with open(ROOT / "eval" / f"{name}.yaml") as f:
        return yaml.safe_load(f)


def run(set_name="gold", engine="local", copilot: Copilot | None = None, gold_executor=None, verbose=False):
    items = load_set(set_name)
    copilot = copilot or Copilot(engine=engine)
    gold_exec = gold_executor or LocalExecutor()
    results = []
    for it in items:
        gold = gold_exec.run(it["sql"])
        ans = copilot.ask(it["question"])
        ok = (not ans.error) and results_match(gold, ans.columns, ans.rows)
        results.append(dict(id=it["id"], category=it.get("category", "dev"), question=it["question"], passed=ok,
                            sql=ans.sql, guard_ok=bool(ans.guard.get("ok")), error=ans.error,
                            elapsed_ms=ans.elapsed_ms, rows=len(ans.rows), gold_rows=len(gold.rows),
                            warnings=ans.warnings))
        if verbose and not ok:
            print(f"\n--- {it['id']} FAIL: {it['question']}\n{ans.sql}\n{ans.error or ''} rows={len(ans.rows)} "
                  f"gold_rows={len(gold.rows)} warn={ans.warnings}")
    n = len(results)
    passed = sum(r["passed"] for r in results)
    by_cat = {}
    for r in results:
        c = by_cat.setdefault(r["category"], [0, 0])
        c[0] += 1
        c[1] += r["passed"]
    lat = sorted(r["elapsed_ms"] for r in results)
    return dict(
        set=set_name, engine=engine, generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        n=n, passed=passed, execution_accuracy=round(passed / n, 4),
        guard_pass_rate=round(sum(r["guard_ok"] for r in results) / n, 4),
        p50_ms=round(statistics.median(lat), 1), p95_ms=round(lat[int(0.95 * (n - 1))], 1),
        by_category={k: dict(n=v[0], passed=v[1]) for k, v in by_cat.items()},
        results=results,
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="gold", choices=["gold", "dev"])
    ap.add_argument("--engine", default="local", choices=["local", "cortex"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.engine == "cortex":
        from .executor import SnowflakeExecutor

        ex = SnowflakeExecutor()
        m = run(args.set, "cortex", Copilot(engine="cortex", executor=ex), gold_executor=ex, verbose=args.verbose)
    else:
        m = run(args.set, "local", verbose=args.verbose)
    out = ROOT / "reports" / f"benchmark_{args.engine}_{args.set}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(m, indent=2, default=str))
    print(f"\n{args.engine} on {args.set}: {m['passed']}/{m['n']} = {m['execution_accuracy']:.1%} execution accuracy")
    print(f"guardrails passed {m['guard_pass_rate']:.0%} of generated SQL · p50 {m['p50_ms']} ms · p95 {m['p95_ms']} ms")
    for k, v in m["by_category"].items():
        print(f"  {k:<11} {v['passed']}/{v['n']}")
    fails = [r for r in m["results"] if not r["passed"]]
    if fails:
        print("misses:")
        for r in fails:
            print(f"  {r['id']}  {r['question']}")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
