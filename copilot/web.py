"""Entry points for the browser demo (Pyodide).

The page loads this package plus the sqlglot wheel into Pyodide, then calls
`ask` and `check`. Execution happens in JavaScript with sql.js against the same
SQLite file the benchmark uses, so the only thing that differs from the CLI is
which SQLite build runs the final statement.
"""
from __future__ import annotations

import json

from . import guard
from .executor import to_sqlite
from .local_analyst import LocalAnalyst, SemanticModel

_analyst: LocalAnalyst | None = None


def init(model_json: str, glossary_json: str, values_json: str) -> None:
    global _analyst
    _analyst = LocalAnalyst(SemanticModel(json.loads(model_json), json.loads(glossary_json)), json.loads(values_json))


def ask(question: str) -> str:
    plan = _analyst.plan(question)
    out = dict(question=question, sql=plan.sql, explanation=plan.explain, warnings=plan.warnings,
               confidence=plan.confidence, topic=plan.topic, guard=None, sqlite=None)
    if plan.sql:
        g = guard.check(plan.sql)
        out["guard"] = g.as_dict()
        if g.ok:
            out["sqlite"] = to_sqlite(g.sql)
    return json.dumps(out)


def check(sql: str) -> str:
    """Guard playground: run arbitrary SQL through the guardrails."""
    g = guard.check(sql)
    out = g.as_dict()
    out["sqlite"] = to_sqlite(g.sql) if g.ok else None
    return json.dumps(out)
