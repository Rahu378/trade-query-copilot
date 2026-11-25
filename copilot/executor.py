"""Run validated SQL against Snowflake, or against the local SQLite copy.

The SQL the copilot produces is Snowflake SQL against TRADE_OPS.CORE. For local
runs sqlglot rewrites it: database/schema prefixes are dropped and the dialect
is transpiled to SQLite. Same query, same answer, no Snowflake account needed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .config import LOCAL_DB, MAX_ROWS, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE


@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    elapsed_ms: float = 0.0
    executed_sql: str = ""
    error: str | None = None


def to_sqlite(sql: str) -> str:
    tree = sqlglot.parse_one(sql, read="snowflake")
    for t in tree.find_all(exp.Table):
        t.set("catalog", None)
        t.set("db", None)
    return tree.sql(dialect="sqlite")


class LocalExecutor:
    name = "sqlite"

    def __init__(self, path=LOCAL_DB):
        import sqlite3  # imported here so the module also loads in Pyodide, which ships without it

        # read-only handle: the guard already blocks writes, this makes it a second lock
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)

    def run(self, sql: str, max_rows: int = MAX_ROWS) -> Result:
        res = Result()
        t0 = time.perf_counter()
        try:
            res.executed_sql = to_sqlite(sql)
            cur = self.con.execute(res.executed_sql)
            res.columns = [d[0] for d in cur.description or []]
            res.rows = [list(r) for r in cur.fetchmany(max_rows)]
        except Exception as e:  # noqa: BLE001 - shown to the user
            res.error = f"{type(e).__name__}: {e}"
        res.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return res


class SnowflakeExecutor:
    """Runs as the COPILOT_READER role (SELECT only, see snowflake/setup.sql)."""

    name = "snowflake"

    def __init__(self, session=None, **connect_kwargs):
        self.session = session
        self.conn = None
        if session is None:
            import snowflake.connector

            connect_kwargs.setdefault("role", SNOWFLAKE_ROLE)
            connect_kwargs.setdefault("warehouse", SNOWFLAKE_WAREHOUSE)
            connect_kwargs.setdefault("session_parameters", {"STATEMENT_TIMEOUT_IN_SECONDS": 60, "QUERY_TAG": "copilot"})
            self.conn = snowflake.connector.connect(**connect_kwargs)

    def run(self, sql: str, max_rows: int = MAX_ROWS) -> Result:
        res = Result(executed_sql=sql)
        t0 = time.perf_counter()
        try:
            if self.session is not None:  # Snowpark (Streamlit in Snowflake)
                df = self.session.sql(sql).limit(max_rows).to_pandas()
                res.columns, res.rows = list(df.columns), df.values.tolist()
            else:
                cur = self.conn.cursor()
                cur.execute(sql)
                res.columns = [d[0] for d in cur.description]
                res.rows = [list(r) for r in cur.fetchmany(max_rows)]
        except Exception as e:  # noqa: BLE001
            res.error = f"{type(e).__name__}: {e}"
        res.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return res
