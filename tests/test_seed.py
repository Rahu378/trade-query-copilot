import datetime as dt

from copilot.config import AS_OF
from copilot.seed import cusip_check, isin_ok, lei_ok


def test_identifiers_have_valid_check_digits(tables):
    for s in tables["dim_security"]:
        assert isin_ok(s["isin"]), s
        if s["cusip"]:
            assert cusip_check(s["cusip"][:8]) == s["cusip"][8]
    for c in tables["dim_counterparty"]:
        assert lei_ok(c["lei"]) and len(c["lei"]) == 20


def test_known_real_cusip():
    assert cusip_check("03783310") == "0"  # Apple 037833100


def test_settlement_states_are_consistent(tables):
    trades = {t["trade_id"]: t for t in tables["fact_trades"]}
    for s in tables["fact_settlements"]:
        t = trades[s["trade_id"]]
        assert t["trade_status"] != "CANCELLED"
        if s["settlement_status"] == "PENDING":
            assert s["expected_settle_date"] > AS_OF
        if s["settlement_status"] == "FAILED":
            assert s["expected_settle_date"] <= AS_OF and s["actual_settle_date"] is None and s["days_failing"] >= 0
        if s["settlement_status"] == "SETTLED":
            assert s["actual_settle_date"] >= s["expected_settle_date"]


def test_long_only_funds_never_go_short(tables):
    assert min(p["traded_quantity"] for p in tables["fact_positions"]) >= 0


def test_t_plus_one_for_us(tables):
    secs = {s["security_id"]: s for s in tables["dim_security"]}
    for t in tables["fact_trades"][:500]:
        if secs[t["security_id"]]["country"] == "US":
            d = t["trade_date"] + dt.timedelta(days=1)
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
            assert t["settle_date"] == d
