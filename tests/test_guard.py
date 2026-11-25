import pytest

from copilot import guard


@pytest.mark.parametrize("sql,why", [
    ("DELETE FROM TRADE_OPS.CORE.FACT_TRADES", "SELECT"),
    ("UPDATE TRADE_OPS.CORE.FACT_BREAKS SET break_status = 'RESOLVED'", "SELECT"),
    ("SELECT 1; DROP TABLE TRADE_OPS.CORE.FACT_TRADES", "one statement"),
    ("SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY", "outside"),
    ("SELECT * FROM TRADE_OPS.CORE.PAYROLL", "allow-list"),
    ("SELECT * FROM OTHER_DB.CORE.FACT_TRADES", "outside"),
    ("SELECT SYSTEM$ALLOWLIST()", "not allowed"),
    ("SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))", "not allowed"),
    ("CREATE TABLE TRADE_OPS.CORE.X AS SELECT * FROM TRADE_OPS.CORE.FACT_TRADES", "SELECT"),
    ("COPY INTO @stage FROM TRADE_OPS.CORE.FACT_TRADES", "SELECT"),
    ("GRANT SELECT ON TRADE_OPS.CORE.FACT_TRADES TO ROLE PUBLIC", "SELECT"),
    ("this is not sql at all )(", ""),
])
def test_blocks(sql, why):
    r = guard.check(sql)
    assert not r.ok
    assert why in r.reason


def test_adds_and_caps_limit():
    assert guard.check("SELECT trade_id FROM TRADE_OPS.CORE.FACT_TRADES").sql.rstrip().endswith("LIMIT 1000")
    r = guard.check("SELECT trade_id FROM TRADE_OPS.CORE.FACT_TRADES LIMIT 50000")
    assert r.ok and "LIMIT 1000" in r.sql
    assert "LIMIT 5" in guard.check("SELECT trade_id FROM TRADE_OPS.CORE.FACT_TRADES LIMIT 5").sql


def test_ctes_and_bare_names_are_fine():
    r = guard.check("WITH f AS (SELECT * FROM FACT_SETTLEMENTS WHERE settlement_status = 'FAILED') "
                    "SELECT COUNT(*) FROM f")
    assert r.ok
    assert [c.name for c in r.checks] == ["single statement", "read-only", "allow-listed tables", "safe functions",
                                          "row limit"]
