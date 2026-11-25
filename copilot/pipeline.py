"""question -> SQL (Cortex Analyst or the local engine) -> guardrails -> execute -> explain."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass, field

from . import guard
from .config import GLOSSARY, LOCAL_DB, SEMANTIC_MODEL
from .executor import LocalExecutor, Result
from .local_analyst import LocalAnalyst, SemanticModel, load_values


@dataclass
class Answer:
    question: str
    engine: str
    sql: str = ""
    guard: dict = field(default_factory=dict)
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    explanation: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    confidence: float | None = None
    suggestions: list = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class Copilot:
    def __init__(self, engine: str = "local", executor=None, analyst: LocalAnalyst | None = None, cortex=None):
        self.engine = engine
        self.executor = executor or LocalExecutor()
        if engine == "local":
            if analyst is None:
                model = SemanticModel.load(SEMANTIC_MODEL, GLOSSARY)
                with sqlite3.connect(f"file:{LOCAL_DB}?mode=ro", uri=True) as con:
                    analyst = LocalAnalyst(model, load_values(con))
            self.analyst = analyst
        else:
            from .cortex import CortexAnalyst

            self.cortex = cortex or CortexAnalyst()
        self.history: list[dict] = []

    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        ans = Answer(question=question, engine=self.engine)
        try:
            if self.engine == "local":
                plan = self.analyst.plan(question)
                ans.sql, ans.explanation, ans.warnings = plan.sql, plan.explain, plan.warnings
                ans.confidence = plan.confidence
                if not plan.sql:
                    ans.error = plan.warnings[0] if plan.warnings else "Couldn't build a query for that."
                    return self._done(ans, t0)
            else:
                reply = self.cortex.ask(question, history=self.history)
                ans.sql, ans.suggestions = reply.sql or "", reply.suggestions
                ans.explanation = [reply.text] if reply.text else []
                ans.confidence = reply.confidence
                self.history = reply.history
                if not reply.sql:
                    ans.error = reply.text or "Cortex Analyst didn't return SQL."
                    return self._done(ans, t0)
            g = guard.check(ans.sql)
            ans.guard = g.as_dict()
            if not g.ok:
                ans.error = f"Blocked by guardrails: {g.reason}"
                return self._done(ans, t0)
            res: Result = self.executor.run(g.sql)
            if res.error:
                ans.error = res.error
            ans.columns, ans.rows = res.columns, res.rows
        except Exception as e:  # noqa: BLE001 - surface it in the UI
            ans.error = f"{type(e).__name__}: {e}"
        return self._done(ans, t0)

    @staticmethod
    def _done(ans: Answer, t0: float) -> Answer:
        ans.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return ans
