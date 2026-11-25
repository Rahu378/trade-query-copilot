"""Synthetic trade-ops warehouse.

Trades, settlements, daily positions (traded vs settled), FX and recon breaks
for ten funds across four desks, Sep 1 to Nov 20 2026.

Outputs:
  data/trade_ops.db   SQLite copy used for local runs, tests and the benchmark
  data/csv/*.csv      the same tables for snowflake/load.sql
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

from .config import AS_OF, DATA_DIR

START = dt.date(2026, 9, 1)

# ticker, isin, name, asset_class, currency, sector, country, approx price
EQUITIES = [
    ("AAPL", "US0378331005", "Apple Inc", "EQUITY", "USD", "Technology", "US", 228.0),
    ("MSFT", "US5949181045", "Microsoft Corp", "EQUITY", "USD", "Technology", "US", 415.0),
    ("NVDA", "US67066G1040", "NVIDIA Corp", "EQUITY", "USD", "Technology", "US", 142.0),
    ("AMZN", "US0231351067", "Amazon.com Inc", "EQUITY", "USD", "Consumer Discretionary", "US", 205.0),
    ("GOOGL", "US02079K3059", "Alphabet Inc Class A", "EQUITY", "USD", "Communication Services", "US", 172.0),
    ("META", "US30303M1027", "Meta Platforms Inc", "EQUITY", "USD", "Communication Services", "US", 575.0),
    ("JPM", "US46625H1005", "JPMorgan Chase & Co", "EQUITY", "USD", "Financials", "US", 238.0),
    ("V", "US92826C8394", "Visa Inc Class A", "EQUITY", "USD", "Financials", "US", 312.0),
    ("JNJ", "US4781601046", "Johnson & Johnson", "EQUITY", "USD", "Health Care", "US", 156.0),
    ("PG", "US7427181091", "Procter & Gamble Co", "EQUITY", "USD", "Consumer Staples", "US", 171.0),
    ("KO", "US1912161007", "Coca-Cola Co", "EQUITY", "USD", "Consumer Staples", "US", 64.0),
    ("XOM", "US30231G1022", "Exxon Mobil Corp", "EQUITY", "USD", "Energy", "US", 118.0),
    ("CVX", "US1667641005", "Chevron Corp", "EQUITY", "USD", "Energy", "US", 158.0),
    ("BAC", "US0605051046", "Bank of America Corp", "EQUITY", "USD", "Financials", "US", 46.0),
    ("PFE", "US7170811035", "Pfizer Inc", "EQUITY", "USD", "Health Care", "US", 26.0),
    ("MRK", "US58933Y1055", "Merck & Co Inc", "EQUITY", "USD", "Health Care", "US", 99.0),
    ("CSCO", "US17275R1023", "Cisco Systems Inc", "EQUITY", "USD", "Technology", "US", 58.0),
    ("ORCL", "US68389X1054", "Oracle Corp", "EQUITY", "USD", "Technology", "US", 182.0),
    ("NFLX", "US64110L1061", "Netflix Inc", "EQUITY", "USD", "Communication Services", "US", 880.0),
    ("WMT", "US9311421039", "Walmart Inc", "EQUITY", "USD", "Consumer Staples", "US", 88.0),
    ("SAP", "DE0007164600", "SAP SE", "EQUITY", "EUR", "Technology", "DE", 228.0),
    ("SIE", "DE0007236101", "Siemens AG", "EQUITY", "EUR", "Industrials", "DE", 182.0),
    ("ASML", "NL0010273215", "ASML Holding NV", "EQUITY", "EUR", "Technology", "NL", 668.0),
    ("MC", "FR0000121014", "LVMH Moet Hennessy", "EQUITY", "EUR", "Consumer Discretionary", "FR", 612.0),
    ("AZN", "GB0009895292", "AstraZeneca plc", "EQUITY", "GBP", "Health Care", "GB", 105.0),
    ("ULVR", "GB00B10RZP78", "Unilever plc", "EQUITY", "GBP", "Consumer Staples", "GB", 47.0),
    ("SHEL", "GB00BP6MXD84", "Shell plc", "EQUITY", "GBP", "Energy", "GB", 25.6),
    ("7203", "JP3633400001", "Toyota Motor Corp", "EQUITY", "JPY", "Consumer Discretionary", "JP", 2780.0),
    ("6758", "JP3435000009", "Sony Group Corp", "EQUITY", "JPY", "Consumer Discretionary", "JP", 3270.0),
    ("SPY", "US78462F1030", "SPDR S&P 500 ETF Trust", "ETF", "USD", "Index", "US", 595.0),
    ("QQQ", "US46090E1038", "Invesco QQQ Trust", "ETF", "USD", "Index", "US", 515.0),
    ("IWM", "US4642876555", "iShares Russell 2000 ETF", "ETF", "USD", "Index", "US", 228.0),
    ("EFA", "US4642874659", "iShares MSCI EAFE ETF", "ETF", "USD", "Index", "US", 81.0),
    ("AGG", "US4642872265", "iShares Core US Aggregate Bond ETF", "ETF", "USD", "Fixed Income", "US", 99.0),
    ("LQD", "US4642872422", "iShares iBoxx IG Corporate Bond ETF", "ETF", "USD", "Fixed Income", "US", 109.0),
]
# synthetic bonds: name, asset_class, currency, country, cusip-ish base (8 chars), clean price
BONDS = [
    ("UST 4.125% 15-Nov-2032", "GOVT_BOND", "USD", "US", "91282CLX", 99.4),
    ("UST 4.500% 15-Nov-2034", "GOVT_BOND", "USD", "US", "91282CMB", 101.2),
    ("UST 3.875% 15-Aug-2030", "GOVT_BOND", "USD", "US", "91282CKZ", 98.7),
    ("DBR 2.600% 15-Aug-2034", "GOVT_BOND", "EUR", "DE", "BU2H34A6", 100.3),
    ("UKT 4.250% 07-Jun-2032", "GOVT_BOND", "GBP", "GB", "B7UK32R1", 99.1),
    ("JGB 1.100% 20-Sep-2034", "GOVT_BOND", "JPY", "JP", "J1Q34S20", 99.8),
    ("Halvorsen Energy 5.750% 2031", "CORP_BOND", "USD", "US", "40623HAB", 102.6),
    ("Meridian Health 4.900% 2030", "CORP_BOND", "USD", "US", "58941MAD", 100.9),
    ("Castellan Telecom 6.100% 2032", "CORP_BOND", "USD", "US", "14819CAF", 103.4),
    ("Arden Logistics 5.400% 2029", "CORP_BOND", "USD", "US", "03980LAC", 101.1),
    ("Brightline Utilities 4.600% 2034", "CORP_BOND", "USD", "US", "10917BAE", 97.8),
    ("Vantage Retail 6.300% 2030", "CORP_BOND", "EUR", "FR", "F9R30VR2", 104.0),
]
COUNTERPARTIES = [
    ("Halden Securities", "US", 1), ("Northgate Capital Markets", "US", 1), ("Brightwater Brokerage", "US", 2),
    ("Kestrel Prime", "GB", 1), ("Oakmont & Lowe", "GB", 2), ("Sable Street Partners", "US", 3),
    ("Marlow Agency Execution", "GB", 2), ("Rhein Kapital AG", "DE", 1), ("Lumiere Valeurs", "FR", 2),
    ("Kaido Securities", "JP", 1), ("Alpenrand Bank", "CH", 2), ("Granite Point Markets", "US", 2),
    ("Thistle & Crane", "GB", 3), ("Pacific Ridge Securities", "US", 3),
]
ACCOUNTS = [
    ("ACC-101", "US Large Cap Core", "US Equity"),
    ("ACC-102", "US Small Cap Growth", "US Equity"),
    ("ACC-201", "Global Equity Fund", "Global Equity"),
    ("ACC-202", "International Value", "Global Equity"),
    ("ACC-203", "European Opportunities", "Global Equity"),
    ("ACC-301", "Core Bond Fund", "Fixed Income"),
    ("ACC-302", "Short Duration Credit", "Fixed Income"),
    ("ACC-303", "Global Government Bond", "Fixed Income"),
    ("ACC-401", "Balanced Allocation", "Multi-Asset"),
    ("ACC-402", "Target Date 2040", "Multi-Asset"),
]
TRADERS = {
    "US Equity": ["mkhan", "jliu", "dpatel"],
    "Global Equity": ["sweber", "akobayashi", "rmoreau"],
    "Fixed Income": ["tobrien", "nrossi", "ehughes"],
    "Multi-Asset": ["cnguyen", "bfischer"],
}
FAIL_REASONS = [
    "Counterparty short of securities", "SSI mismatch", "Counterparty insufficient cash",
    "Late matching instruction", "Place of settlement mismatch", "Unmatched trade details",
]
FX_START = {"USD": 1.0, "EUR": 1.0842, "GBP": 1.2716, "JPY": 0.006631}
ROOT_CAUSES = {
    "TIMING": "Reconciliations", "MISSED_CA": "Corporate Actions", "STALE_PRICE": "Pricing & Valuations",
    "FX": "Market Data - FX", "FAILED_SETTLEMENT": "Settlements", "BOOKING_ERROR": "Trade Support",
}


def _char_val(c: str) -> int:
    if c.isdigit():
        return int(c)
    return {"*": 36, "@": 37, "#": 38}.get(c, ord(c.upper()) - 55)


def cusip_check(base8: str) -> str:
    total = 0
    for i, c in enumerate(base8):
        v = _char_val(c)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def isin_check(base11: str) -> str:
    digits = "".join(str(int(c, 36)) for c in base11)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def isin_ok(isin: str) -> bool:
    return isin_check(isin[:11]) == isin[11]


def lei(rng: random.Random) -> str:
    alnum = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    base = "".join(rng.choice(alnum) for _ in range(4)) + "00" + "".join(rng.choice(alnum) for _ in range(12))
    num = int("".join(str(int(c, 36)) for c in base) + "00")
    return base + f"{98 - num % 97:02d}"


def lei_ok(code: str) -> bool:
    return int("".join(str(int(c, 36)) for c in code)) % 97 == 1


def bdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def add_bdays(d: dt.date, n: int) -> dt.date:
    step = 1 if n >= 0 else -1
    k = 0
    while k != n:
        d += dt.timedelta(days=step)
        if d.weekday() < 5:
            k += step
    return d


def bday_diff(a: dt.date, b: dt.date) -> int:
    """business days from a to b (b after a)."""
    return max(0, len(bdays(a, b)) - 1)


def build(seed: int = 11) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    days = bdays(START, AS_OF)

    # ---------------- securities
    secs = []
    sid = 5001
    for t, isin, name, ac, ccy, sector, ctry, px in EQUITIES:
        assert isin_ok(isin), isin
        secs.append(dict(security_id=sid, ticker=t, isin=isin, cusip=isin[2:11] if isin.startswith("US") else None,
                         security_name=name, asset_class=ac, currency=ccy, sector=sector, country=ctry, _px=px))
        sid += 1
    for name, ac, ccy, ctry, base8, px in BONDS:
        cusip = base8 + cusip_check(base8)
        prefix = {"US": "US", "DE": "DE", "GB": "GB", "JP": "JP", "FR": "FR"}[ctry]
        isin = prefix + cusip + isin_check(prefix + cusip)
        ticker = name.split(" ")[0] if ac == "GOVT_BOND" else "".join(w[0] for w in name.split(" ")[:2])
        ticker = f"{ticker} {name.split(' ')[-1]}"
        secs.append(dict(security_id=sid, ticker=ticker, isin=isin, cusip=cusip if ctry == "US" else None,
                         security_name=name, asset_class=ac, currency=ccy,
                         sector="Government" if ac == "GOVT_BOND" else "Corporate", country=ctry, _px=px))
        sid += 1
    by_id = {s["security_id"]: s for s in secs}

    # ---------------- market data
    px = {}
    for s in secs:
        p = s["_px"] * rng.uniform(0.93, 1.03)
        vol = 0.004 if "BOND" in s["asset_class"] else 0.014
        ser = {}
        for d in days:
            p *= 1 + rng.gauss(0.0003, vol)
            ser[d] = round(p, 4)
        px[s["security_id"]] = ser
    fx = {}
    for ccy, r in FX_START.items():
        ser = {}
        for d in days:
            if ccy != "USD":
                r *= 1 + rng.gauss(0, 0.004)
            ser[d] = round(r, 8)
        fx[ccy] = ser
    fx_rows = [dict(rate_date=d, currency=c, usd_rate=fx[c][d]) for d in days for c in fx if c != "USD"]

    # ---------------- reference data
    cps = []
    for i, (name, ctry, tier) in enumerate(COUNTERPARTIES):
        code = lei(rng)
        assert lei_ok(code)
        cps.append(dict(counterparty_id=f"CP{101 + i}", counterparty_name=name, lei=code, country=ctry, tier=tier))
    accts = [dict(account_id=a, account_name=n, desk=d, base_currency="USD") for a, n, d in ACCOUNTS]
    desk_of = {a: d for a, _, d in ACCOUNTS}

    def universe(desk):
        if desk == "US Equity":
            return [s for s in secs if s["asset_class"] in ("EQUITY", "ETF") and s["currency"] == "USD"
                    and s["sector"] != "Fixed Income"]
        if desk == "Global Equity":
            return [s for s in secs if s["asset_class"] in ("EQUITY", "ETF") and s["sector"] != "Fixed Income"]
        if desk == "Fixed Income":
            return [s for s in secs if "BOND" in s["asset_class"] or s["sector"] == "Fixed Income"]
        return secs

    mult = lambda s: 0.01 if "BOND" in s["asset_class"] else 1.0  # noqa: E731
    lot = lambda s: 1000 if "BOND" in s["asset_class"] else (100 if s["country"] == "JP" else 1)  # noqa: E731

    # opening holdings so sells never go short
    holdings: dict[tuple[str, int], int] = defaultdict(int)
    for a in accts:
        uni = universe(a["desk"])
        for s in rng.sample(uni, min(len(uni), rng.randint(14, 22))):
            usd = rng.uniform(1.5e6, 9e6)
            q = usd / (s["_px"] * mult(s) * FX_START[s["currency"]])
            step = 100_000 if "BOND" in s["asset_class"] else (100 if q > 2000 else 10)
            holdings[(a["account_id"], s["security_id"])] = max(step, int(q // step) * step)
    opening = dict(holdings)

    # ---------------- trades + settlements
    trades, setts = [], []
    tid = 7_310_000
    cp_w = [{1: 5, 2: 3, 3: 1.4}[c["tier"]] for c in cps]
    acct_w = [1.4, 0.8, 1.3, 0.9, 0.8, 1.1, 0.8, 0.9, 1.0, 0.7]
    for d in days:
        n = rng.randint(52, 84)
        for _ in range(n):
            a = rng.choices(accts, weights=acct_w)[0]
            desk = a["desk"]
            held = [s for (acc, sec), q in holdings.items() if acc == a["account_id"] and q > 0
                    for s in [by_id[sec]]]
            uni = universe(desk)
            s = rng.choice(held) if held and rng.random() < 0.7 else rng.choice(uni)
            cp = rng.choices(cps, weights=cp_w)[0]
            target = math.exp(rng.gauss(math.log(380_000), 1.0))
            target = min(max(target, 15_000), 9_000_000)
            if "BOND" in s["asset_class"]:
                target *= 2.2
            price = px[s["security_id"]][d] * rng.uniform(0.997, 1.003)
            q = target / (price * mult(s) * fx[s["currency"]][d])
            step = lot(s) if "BOND" not in s["asset_class"] else 50_000
            q = max(step, int(q // step) * step)
            key = (a["account_id"], s["security_id"])
            side = "BUY"
            if holdings[key] > q * 1.2 and rng.random() < 0.46:
                side = "SELL"
            cycle = 1 if s["country"] in ("US", "CA") else 2
            settle = add_bdays(d, cycle)
            status = "CANCELLED" if rng.random() < 0.015 else "BOOKED"
            tid += rng.randint(1, 23)
            notional_local = round(q * price * mult(s), 2)
            notional_usd = round(notional_local * fx[s["currency"]][d], 2)
            trades.append(dict(trade_id=f"T{tid}", trade_date=d, settle_date=settle, account_id=a["account_id"],
                               security_id=s["security_id"], counterparty_id=cp["counterparty_id"], side=side,
                               quantity=q, price=round(price, 4), trade_ccy=s["currency"],
                               notional_local=notional_local, notional_usd=notional_usd,
                               trader=rng.choice(TRADERS[desk]), trade_status=status))
            if status == "CANCELLED":
                continue
            holdings[key] += q if side == "BUY" else -q
            # settlement outcome
            p_fail = 0.035 * {1: 0.8, 2: 1.1, 3: 2.3}[cp["tier"]] * (1.6 if "BOND" in s["asset_class"] else 1.0)
            p_fail *= 1.5 if notional_usd > 1_000_000 else 1.0
            fails = rng.random() < p_fail
            if settle > AS_OF:
                st = dict(settlement_status="PENDING", actual_settle_date=None, fail_reason=None, days_failing=None)
            elif not fails:
                st = dict(settlement_status="SETTLED", actual_settle_date=settle, fail_reason=None, days_failing=None)
            else:
                late = rng.choice([1, 1, 2, 2, 3, 4, 6, 9])
                actual = add_bdays(settle, late)
                reason = rng.choice(FAIL_REASONS)
                if actual > AS_OF:
                    st = dict(settlement_status="FAILED", actual_settle_date=None, fail_reason=reason,
                              days_failing=bday_diff(settle, AS_OF))
                else:
                    st = dict(settlement_status="SETTLED", actual_settle_date=actual, fail_reason=reason,
                              days_failing=None)
            setts.append(dict(trade_id=f"T{tid}", expected_settle_date=settle, **st))

    # ---------------- daily positions for the last week
    sett_by_trade = {x["trade_id"]: x for x in setts}
    snap_days = bdays(add_bdays(AS_OF, -4), AS_OF)
    positions = []
    keys = sorted(set(opening) | {(t["account_id"], t["security_id"]) for t in trades})
    for d in snap_days:
        traded = dict(opening)
        settled = dict(opening)
        for t in trades:
            if t["trade_status"] == "CANCELLED" or t["trade_date"] > d:
                continue
            k = (t["account_id"], t["security_id"])
            sgn = 1 if t["side"] == "BUY" else -1
            traded[k] = traded.get(k, 0) + sgn * t["quantity"]
            s = sett_by_trade[t["trade_id"]]
            if s["actual_settle_date"] and s["actual_settle_date"] <= d:
                settled[k] = settled.get(k, 0) + sgn * t["quantity"]
        for k in keys:
            tq, sq = traded.get(k, 0), settled.get(k, 0)
            if tq == 0 and sq == 0:
                continue
            s = by_id[k[1]]
            p = px[s["security_id"]][d]
            rate = fx[s["currency"]][d]
            positions.append(dict(
                as_of_date=d, account_id=k[0], security_id=k[1], traded_quantity=tq, settled_quantity=sq,
                price=p, currency=s["currency"],
                market_value_local=round(tq * p * mult(s), 2),
                market_value_usd=round(tq * p * mult(s) * rate, 2),
                settled_market_value_usd=round(sq * p * mult(s) * rate, 2)))

    # ---------------- recon breaks
    breaks = []
    bid = 40_100
    open_days = bdays(dt.date(2026, 10, 1), AS_OF)
    held_keys = [k for k, q in opening.items() if q > 0]
    for _ in range(176):
        opened = rng.choice(open_days)
        cause = rng.choices(list(ROOT_CAUSES), weights=[24, 14, 12, 10, 22, 18])[0]
        btype = {"STALE_PRICE": "PRICE", "FX": "PRICE"}.get(cause, rng.choice(["POSITION", "POSITION", "CASH"]))
        acct, sec = rng.choice(held_keys)
        age_open = bday_diff(opened, AS_OF)
        if age_open > 9 or (age_open > 3 and rng.random() < 0.6) or (age_open <= 3 and rng.random() < 0.25):
            res = add_bdays(opened, rng.randint(1, max(1, min(age_open, 8)))) if age_open >= 1 else AS_OF
            res = min(res, AS_OF)
            status, resolved, age = "RESOLVED", res, bday_diff(opened, res)
        else:
            status = "INVESTIGATING" if rng.random() < 0.45 else "OPEN"
            resolved, age = None, age_open
        bid += rng.randint(1, 9)
        amount = round(min(4_800_000, math.exp(rng.gauss(math.log(85_000), 1.1))), 2)
        breaks.append(dict(break_id=f"BRK-{bid}", opened_date=opened, resolved_date=resolved,
                           account_id=acct, security_id=None if btype == "CASH" else sec, break_type=btype,
                           break_status=status, amount_usd=amount, age_days=age, owner_team=ROOT_CAUSES[cause],
                           root_cause=cause))

    for s in secs:
        s.pop("_px")
    return {
        "dim_account": accts,
        "dim_counterparty": cps,
        "dim_security": secs,
        "fx_rates": fx_rows,
        "fact_trades": trades,
        "fact_settlements": setts,
        "fact_positions": positions,
        "fact_breaks": breaks,
    }


COLUMN_TYPES = {
    "dim_account": "account_id TEXT PRIMARY KEY, account_name TEXT, desk TEXT, base_currency TEXT",
    "dim_counterparty": "counterparty_id TEXT PRIMARY KEY, counterparty_name TEXT, lei TEXT, country TEXT, tier INTEGER",
    "dim_security": ("security_id INTEGER PRIMARY KEY, ticker TEXT, isin TEXT, cusip TEXT, security_name TEXT, "
                     "asset_class TEXT, currency TEXT, sector TEXT, country TEXT"),
    "fx_rates": "rate_date TEXT, currency TEXT, usd_rate REAL",
    "fact_trades": ("trade_id TEXT PRIMARY KEY, trade_date TEXT, settle_date TEXT, account_id TEXT, security_id INTEGER, "
                    "counterparty_id TEXT, side TEXT, quantity INTEGER, price REAL, trade_ccy TEXT, "
                    "notional_local REAL, notional_usd REAL, trader TEXT, trade_status TEXT"),
    "fact_settlements": ("trade_id TEXT PRIMARY KEY, expected_settle_date TEXT, settlement_status TEXT, "
                         "actual_settle_date TEXT, fail_reason TEXT, days_failing INTEGER"),
    "fact_positions": ("as_of_date TEXT, account_id TEXT, security_id INTEGER, traded_quantity INTEGER, "
                       "settled_quantity INTEGER, price REAL, currency TEXT, market_value_local REAL, "
                       "market_value_usd REAL, settled_market_value_usd REAL"),
    "fact_breaks": ("break_id TEXT PRIMARY KEY, opened_date TEXT, resolved_date TEXT, account_id TEXT, "
                    "security_id INTEGER, break_type TEXT, break_status TEXT, amount_usd REAL, age_days INTEGER, "
                    "owner_team TEXT, root_cause TEXT"),
}


def _val(v):
    return v.isoformat() if isinstance(v, dt.date) else v


def write_sqlite(tables: dict[str, list[dict]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    for name, rows in tables.items():
        con.execute(f"CREATE TABLE {name} ({COLUMN_TYPES[name]})")
        cols = [c.split()[0] for c in COLUMN_TYPES[name].split(", ")]
        con.executemany(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[_val(r.get(c)) for c in cols] for r in rows])
    con.commit()
    con.close()


def write_csv(tables: dict[str, list[dict]], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        cols = [c.split()[0] for c in COLUMN_TYPES[name].split(", ")]
        with open(folder / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c.upper() for c in cols])
            for r in rows:
                w.writerow(["" if r.get(c) is None else _val(r.get(c)) for c in cols])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    tables = build(args.seed)
    write_sqlite(tables, DATA_DIR / "trade_ops.db")
    write_csv(tables, DATA_DIR / "csv")
    for k, v in tables.items():
        print(f"{k:<18} {len(v):>6}")


if __name__ == "__main__":
    main()
