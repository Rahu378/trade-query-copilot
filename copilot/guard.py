"""SQL guardrails. Every statement goes through here before it touches a warehouse,
whether Cortex Analyst wrote it or the local engine did.

Checks, in order:
  1. parses as Snowflake SQL, exactly one statement
  2. read-only: a SELECT (or UNION of SELECTs), no DML/DDL/COPY/CALL/... anywhere in the tree
  3. tables: only TRADE_OPS.CORE tables on the allow-list (CTE names are fine)
  4. functions: no SYSTEM$ / external / side-effecting functions
  5. row limit: LIMIT added if missing, capped at MAX_ROWS if larger
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .config import DATABASE, MAX_ROWS, SCHEMA

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # it warns on Snowflake syntax it falls back on

ALLOWED_TABLES = frozenset({
    "FACT_TRADES", "FACT_SETTLEMENTS", "FACT_POSITIONS", "FACT_BREAKS",
    "DIM_ACCOUNT", "DIM_COUNTERPARTY", "DIM_SECURITY", "FX_RATES",
})
_WRITE_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Merge", "Command", "TruncateTable", "Grant",
    "Copy", "Set", "Use", "Transaction", "Commit", "Rollback", "Into", "Describe", "Show",
) if hasattr(exp, n))
_BANNED_FUNC_PREFIXES = ("system$", "snowflake.", "external_", "parse_url")
_BANNED_FUNCS = {"call", "execute", "result_scan", "generator", "seq4", "seq8", "randstr", "uniform", "sleep"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class GuardResult:
    ok: bool
    sql: str
    checks: list[Check] = field(default_factory=list)

    @property
    def reason(self) -> str:
        bad = [c for c in self.checks if not c.ok]
        return bad[0].detail if bad else ""

    def as_dict(self) -> dict:
        return dict(ok=self.ok, sql=self.sql, checks=[c.__dict__ for c in self.checks])


def check(sql: str, max_rows: int = MAX_ROWS) -> GuardResult:
    checks: list[Check] = []

    def fail(name, detail):
        checks.append(Check(name, False, detail))
        return GuardResult(False, sql, checks)

    try:
        parsed = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except sqlglot.errors.ParseError as e:
        return fail("parses", f"not valid SQL: {str(e).splitlines()[0]}")
    if len(parsed) != 1:
        return fail("single statement", f"expected one statement, got {len(parsed)}")
    checks.append(Check("single statement", True))
    tree = parsed[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return fail("read-only", f"only SELECT is allowed, got {type(tree).__name__.upper()}")
    for node in tree.walk():
        if isinstance(node, _WRITE_NODES):
            return fail("read-only", f"{type(node).__name__.upper()} is not allowed")
    checks.append(Check("read-only", True, "SELECT only"))

    ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    seen = []
    for t in tree.find_all(exp.Table):
        name = t.name.upper()
        if name in ctes:
            continue
        db = (t.args.get("catalog").name if t.args.get("catalog") else DATABASE).upper()
        schema = (t.args.get("db").name if t.args.get("db") else SCHEMA).upper()
        if db != DATABASE or schema != SCHEMA:
            return fail("allow-listed tables", f"{db}.{schema}.{name} is outside {DATABASE}.{SCHEMA}")
        if name not in ALLOWED_TABLES:
            return fail("allow-listed tables", f"table {name} is not on the allow-list")
        seen.append(name)
    checks.append(Check("allow-listed tables", True, ", ".join(sorted(set(seen)))))

    for f in tree.find_all(exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname in _BANNED_FUNCS or fname.startswith(_BANNED_FUNC_PREFIXES):
            return fail("safe functions", f"{fname}() is not allowed")
    for d in tree.find_all(exp.Dot):
        if d.sql(dialect="snowflake").lower().startswith(_BANNED_FUNC_PREFIXES):
            return fail("safe functions", f"{d.sql(dialect='snowflake')} is not allowed")
    checks.append(Check("safe functions", True))

    if isinstance(tree, exp.Select):
        lim = tree.args.get("limit")
        if lim is None:
            tree = tree.limit(max_rows)
            detail = f"LIMIT {max_rows} added"
        else:
            try:
                n = int(lim.expression.name)
            except (AttributeError, ValueError):
                n = max_rows + 1
            if n > max_rows:
                tree = tree.limit(max_rows)
                detail = f"LIMIT {n} capped to {max_rows}"
            else:
                detail = f"LIMIT {n} kept"
    else:
        tree = exp.select("*").from_(tree.subquery("u")).limit(max_rows)
        detail = f"LIMIT {max_rows} added"
    checks.append(Check("row limit", True, detail))
    return GuardResult(True, tree.sql(dialect="snowflake", pretty=True), checks)
