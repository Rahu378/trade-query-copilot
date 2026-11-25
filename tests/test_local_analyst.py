import datetime as dt

import pytest
import yaml

from copilot import guard
from copilot.benchmark import results_match
from copilot.config import ROOT
from copilot.local_analyst import parse_window

DEV = yaml.safe_load(open(ROOT / "eval" / "dev.yaml"))


@pytest.mark.parametrize("item", DEV, ids=[d["id"] for d in DEV])
def test_dev_set(item, copilot, executor):
    ans = copilot.ask(item["question"])
    assert not ans.error, ans.error
    assert results_match(executor.run(item["sql"]), ans.columns, ans.rows), ans.sql


@pytest.mark.parametrize("text,start,end", [
    ("today", "2026-11-20", "2026-11-20"),
    ("yesterday", "2026-11-19", "2026-11-19"),
    ("this week", "2026-11-16", "2026-11-20"),
    ("last week", "2026-11-09", "2026-11-13"),
    ("this month", "2026-11-01", "2026-11-20"),
    ("last month", "2026-10-01", "2026-10-31"),
    ("last 5 business days", "2026-11-16", "2026-11-20"),
    ("on nov 18", "2026-11-18", "2026-11-18"),
])
def test_windows(text, start, end):
    s, e, _, _ = parse_window(f" {text} ", dt.date(2026, 11, 20))
    assert (str(s), str(e)) == (start, end)


def test_settlement_windows_use_due_date(analyst):
    p = analyst.plan("How many trades failed to settle this week?")
    assert "s.expected_settle_date BETWEEN '2026-11-16' AND '2026-11-20'" in p.sql
    assert "trade_date" not in p.sql


def test_cancelled_trades_excluded_by_default(analyst):
    assert "t.trade_status <> 'CANCELLED'" in analyst.plan("Total notional this week").sql
    assert "t.trade_status = 'CANCELLED'" in analyst.plan("How many trades were cancelled today?").sql


def test_positions_default_to_latest_snapshot(analyst):
    assert "p.as_of_date = '2026-11-20'" in analyst.plan("Market value by account").sql


def test_usd_as_unit_is_not_a_filter(analyst):
    assert "trade_ccy" not in analyst.plan("Average trade size in USD this month").sql


def test_unknown_topic_is_refused(analyst):
    p = analyst.plan("What's the weather like?")
    assert not p.sql and p.warnings


def test_everything_generated_passes_the_guard(analyst):
    for item in DEV:
        p = analyst.plan(item["question"])
        assert guard.check(p.sql).ok, item["id"]


def test_explanations_are_there(copilot):
    ans = copilot.ask("Which trades over $1M failed to settle this week, by counterparty?")
    text = " ".join(ans.explanation)
    assert "Failed" in text and "notional_usd > $1,000,000" in text and "counterparty" in text
