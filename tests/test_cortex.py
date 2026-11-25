import pytest

from copilot import cortex
from copilot.pipeline import Copilot

PAYLOAD = {
    "request_id": "r-1",
    "message": {"role": "analyst", "content": [
        {"type": "text", "text": "This is our interpretation of your question: failed trades over $1M this week."},
        {"type": "sql", "statement": "SELECT COUNT(*) FROM TRADE_OPS.CORE.FACT_SETTLEMENTS WHERE settlement_status = 'FAILED'",
         "confidence": {"verified_query_used": {"name": "failed_large_trades_this_week_by_counterparty"}}},
        {"type": "suggestions", "suggestions": ["Failed trades by desk"]},
    ]},
    "warnings": [{"message": "table FX_RATES has no relationships"}],
}


def test_parse_response():
    r = cortex.parse_response(PAYLOAD, [{"role": "user", "content": [{"type": "text", "text": "q"}]}])
    assert r.sql.startswith("SELECT COUNT(*)")
    assert r.verified_query == "failed_large_trades_this_week_by_counterparty"
    assert r.suggestions == ["Failed trades by desk"]
    assert r.history[-1]["role"] == "analyst" and len(r.history) == 2
    assert r.warnings == ["table FX_RATES has no relationships"]


class FakeResp:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_rest_call_shape(monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json, headers=headers)
        return FakeResp(PAYLOAD)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    c = cortex.CortexAnalyst(account_url="https://acme-x1.snowflakecomputing.com", token="t0k")
    c.ask("Which trades failed?")
    assert seen["url"] == "https://acme-x1.snowflakecomputing.com/api/v2/cortex/analyst/message"
    assert seen["headers"]["Authorization"] == "Bearer t0k"
    assert seen["headers"]["X-Snowflake-Authorization-Token-Type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    assert seen["body"]["semantic_model_file"].endswith("trade_ops.yaml")
    assert seen["body"]["messages"][-1]["content"][0]["text"] == "Which trades failed?"


class EvilCortex:
    """Whatever the model returns still has to get past the guardrails."""

    def ask(self, question, history=None):
        return cortex.AnalystReply(text="sure", sql="DELETE FROM TRADE_OPS.CORE.FACT_TRADES", history=[])


def test_cortex_sql_goes_through_the_guard(executor):
    cp = Copilot(engine="cortex", executor=executor, cortex=EvilCortex())
    ans = cp.ask("delete everything")
    assert ans.error.startswith("Blocked by guardrails")
    assert ans.rows == []


def test_missing_credentials_are_explicit(monkeypatch):
    with pytest.raises(RuntimeError, match="SNOWFLAKE_ACCOUNT_URL"):
        cortex.CortexAnalyst(account_url="", token="")
