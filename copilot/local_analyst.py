"""Offline NL -> SQL engine driven by the semantic model and glossary.

This is not Cortex Analyst and doesn't pretend to be. It's a small rule-based
parser that reads the same semantic model YAML (tables, synonyms, sample values,
filters, relationships) plus the glossary, and builds Snowflake SQL from it.

Why have it at all:
  * the whole pipeline (guardrails, execution, explanation, benchmark, UI) runs
    without a Snowflake account,
  * it's a baseline: if Cortex can't beat a few hundred lines of rules on the
    gold set, the semantic model is the problem, not the LLM,
  * it runs in the browser (Pyodide) for the GitHub Pages demo.

Pipeline: dates -> thresholds -> topic -> entity values -> group-by -> status
filters -> metric/order/limit -> defaults from the glossary -> SQL.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .config import AS_OF

ALIAS = {"trades": "t", "settlements": "s", "positions": "p", "breaks": "b", "accounts": "a",
         "counterparties": "c", "securities": "x"}
TIME_COL = {"trades": ("trades", "trade_date"), "positions": ("positions", "as_of_date"),
            "breaks": ("breaks", "opened_date"), "settlement": ("settlements", "expected_settle_date")}
STOP = set("""a an the of for in on at to by per with and or is are was were be been do did does done we our us
our me my you your i it its this that these those there their what which who whom how many much show list give
display tell get find all any each every from as than vs versus over under total sum count number average avg
mean today yesterday week month last this top highest lowest most least largest biggest smallest oldest newest
have has had hold holds held so far between into out up down where when within about please let see can could
would should will just only also more less greater bigger fewer same different did ones one
what's whats it's there's that's who's how's we're we've""".split())


@dataclass
class Plan:
    question: str
    topic: str = ""
    base: str = ""
    need: set = field(default_factory=set)
    inner: set = field(default_factory=set)
    select: list = field(default_factory=list)      # (expr, alias)
    where: list = field(default_factory=list)
    group: list = field(default_factory=list)
    order: list = field(default_factory=list)
    limit: int | None = None
    distinct: bool = False
    explain: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    window: tuple | None = None
    sql: str = ""
    confidence: float = 1.0

    def add_where(self, cond: str, why: str | None = None):
        if cond not in self.where:
            self.where.append(cond)
            if why:
                self.explain.append(why)


def _bday(d: dt.date, n: int) -> dt.date:
    step = 1 if n >= 0 else -1
    k = 0
    while k != n:
        d += dt.timedelta(days=step)
        if d.weekday() < 5:
            k += step
    return d


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_window(text: str, as_of: dt.date = AS_OF):
    """Return (start, end, label, span) for the first time expression found, else None."""
    monday = as_of - dt.timedelta(days=as_of.weekday())
    first = as_of.replace(day=1)
    prev_last = first - dt.timedelta(days=1)
    rules = [
        (r"\b(?:so far )?today\b", lambda m: (as_of, as_of, "today")),
        (r"\byesterday\b", lambda m: (_bday(as_of, -1), _bday(as_of, -1), "yesterday")),
        (r"\b(?:this week|week to date|wtd|so far this week)\b", lambda m: (monday, as_of, "this week")),
        (r"\b(?:last|previous|prior) week\b",
         lambda m: (monday - dt.timedelta(days=7), monday - dt.timedelta(days=3), "last week")),
        (r"\b(?:this month|month to date|mtd|so far this month)\b", lambda m: (first, as_of, "this month")),
        (r"\b(?:last|previous|prior) month\b", lambda m: (prev_last.replace(day=1), prev_last, "last month")),
        (r"\b(?:last|past|previous) (\d+) (?:business|trading|working) days\b",
         lambda m: (_bday(as_of, -(int(m.group(1)) - 1)), as_of, f"last {m.group(1)} business days")),
        (r"\b(?:last|past|previous) (\d+) days\b",
         lambda m: (as_of - dt.timedelta(days=int(m.group(1)) - 1), as_of, f"last {m.group(1)} days")),
        (r"\b(?:this quarter|qtd|quarter to date)\b",
         lambda m: (dt.date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1), as_of, "this quarter")),
        (r"\b(?:this year|ytd|year to date)\b", lambda m: (dt.date(as_of.year, 1, 1), as_of, "this year")),
        (r"\b(\d{4})-(\d{2})-(\d{2})\b",
         lambda m: (dt.date(*map(int, m.groups())),) * 2 + (m.group(0),)),
        (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?\b",
         lambda m: (dt.date(as_of.year, MONTHS[m.group(1)], int(m.group(2))),) * 2 + (m.group(0),)),
        (r"\b(\d{1,2})(?:st|nd|rd|th)? (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
         lambda m: (dt.date(as_of.year, MONTHS[m.group(2)], int(m.group(1))),) * 2 + (m.group(0),)),
    ]
    for pat, fn in rules:
        m = re.search(pat, text)
        if m:
            start, end, label = fn(m)
            return start, end, label, m.span()
    return None


AMOUNT = re.compile(
    r"(?P<op>over|above|more than|greater than|exceeding|bigger than|larger than|older than|at least|under|below|"
    r"less than|smaller than|younger than|newer than|at most|>=|<=|>|<)\s*"
    r"(?P<cur>\$|usd\s*)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>k|thousand|m|mm|mn|million|bn|b|billion|business days?|days?)?\b(?P<usd>\s*(?:usd|dollars))?")
OPS = {"over": ">", "above": ">", "more than": ">", "greater than": ">", "exceeding": ">", "bigger than": ">",
       "larger than": ">", "older than": ">", "at least": ">=", "under": "<", "below": "<", "less than": "<",
       "smaller than": "<", "younger than": "<", "newer than": "<", "at most": "<=", ">=": ">=", "<=": "<=",
       ">": ">", "<": "<"}
MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "b": 1e9,
        "billion": 1e9}

DIM_WORDS = [
    # phrase, (table, column)
    ("counterparty tier", ("counterparties", "tier")), ("tier", ("counterparties", "tier")),
    ("counterparty country", ("counterparties", "country")),
    ("counterparty", ("counterparties", "counterparty_name")), ("counterparties", ("counterparties", "counterparty_name")),
    ("broker", ("counterparties", "counterparty_name")), ("brokers", ("counterparties", "counterparty_name")),
    ("cpty", ("counterparties", "counterparty_name")),
    ("account", ("accounts", "account_name")), ("accounts", ("accounts", "account_name")),
    ("fund", ("accounts", "account_name")), ("funds", ("accounts", "account_name")),
    ("portfolio", ("accounts", "account_name")), ("portfolios", ("accounts", "account_name")),
    ("desk", ("accounts", "desk")), ("desks", ("accounts", "desk")),
    ("security", ("securities", "ticker")), ("securities", ("securities", "ticker")),
    ("ticker", ("securities", "ticker")), ("tickers", ("securities", "ticker")), ("stock", ("securities", "ticker")),
    ("stocks", ("securities", "ticker")), ("holding", ("securities", "ticker")), ("holdings", ("securities", "ticker")),
    ("instrument", ("securities", "ticker")), ("instruments", ("securities", "ticker")),
    ("name", ("securities", "ticker")), ("names", ("securities", "ticker")),
    ("asset class", ("securities", "asset_class")), ("asset classes", ("securities", "asset_class")),
    ("asset type", ("securities", "asset_class")),
    ("sector", ("securities", "sector")), ("sectors", ("securities", "sector")), ("industry", ("securities", "sector")),
    ("country", ("counterparties", "country")),
    ("currency", ("*", "currency")), ("currencies", ("*", "currency")), ("ccy", ("*", "currency")),
    ("side", ("trades", "side")), ("direction", ("trades", "side")),
    ("trader", ("trades", "trader")), ("traders", ("trades", "trader")),
    ("fail reason", ("settlements", "fail_reason")), ("fail reasons", ("settlements", "fail_reason")),
    ("reason", ("settlements", "fail_reason")), ("reasons", ("settlements", "fail_reason")),
    ("owner team", ("breaks", "owner_team")), ("owner", ("breaks", "owner_team")), ("team", ("breaks", "owner_team")),
    ("teams", ("breaks", "owner_team")),
    ("break type", ("breaks", "break_type")), ("type", ("breaks", "break_type")),
    ("root cause", ("breaks", "root_cause")), ("cause", ("breaks", "root_cause")),
    ("status", ("*", "status")),
    ("day", ("*", "date")), ("date", ("*", "date")), ("days", ("*", "date")),
]
DIM_WORDS.sort(key=lambda x: -len(x[0]))

LIST_COLS = {
    "trades": [("trades", "trade_id"), ("trades", "trade_date"), ("securities", "ticker"), ("trades", "side"),
               ("trades", "quantity"), ("trades", "notional_usd"), ("counterparties", "counterparty_name")],
    "settlement": [("trades", "trade_id"), ("trades", "trade_date"), ("securities", "ticker"), ("trades", "side"),
                   ("trades", "notional_usd"), ("counterparties", "counterparty_name"),
                   ("settlements", "expected_settle_date"), ("settlements", "settlement_status"),
                   ("settlements", "fail_reason"), ("settlements", "days_failing")],
    "positions": [("accounts", "account_name"), ("securities", "ticker"), ("positions", "traded_quantity"),
                  ("positions", "settled_quantity"), ("positions", "market_value_usd")],
    "breaks": [("breaks", "break_id"), ("breaks", "opened_date"), ("accounts", "account_name"),
               ("breaks", "break_type"), ("breaks", "break_status"), ("breaks", "amount_usd"),
               ("breaks", "age_days"), ("breaks", "owner_team"), ("breaks", "root_cause")],
    "counterparties": [("counterparties", "counterparty_name"), ("counterparties", "country"),
                       ("counterparties", "tier")],
    "accounts": [("accounts", "account_name"), ("accounts", "desk")],
    "securities": [("securities", "ticker"), ("securities", "security_name"), ("securities", "asset_class")],
}
BASE_ENTITY = {"trades": r"trades?|deals?|tickets?|executions?|bookings?", "breaks": r"breaks?|exceptions?",
               "positions": r"positions?|holdings?|lines?"}


class SemanticModel:
    def __init__(self, model: dict, glossary: dict | None = None):
        self.raw = model
        self.glossary = glossary or {}
        self.tables = {t["name"]: t for t in model["tables"]}
        self.physical = {n: "{database}.{schema}.{table}".format(**t["base_table"]) for n, t in self.tables.items()}
        self.rel = {}
        for r in model.get("relationships", []):
            c = r["relationship_columns"][0]
            self.rel[(r["left_table"], r["right_table"])] = (c["left_column"], c["right_column"], r["join_type"])
        self.filters = []  # (synonym, table, name, expr)
        for t in model["tables"]:
            for f in t.get("filters", []):
                for syn in f.get("synonyms", []) + [f["name"].replace("_", " ")]:
                    self.filters.append((syn.lower(), t["name"], f["name"], f["expr"]))

    @classmethod
    def load(cls, model_path, glossary_path=None):
        import yaml

        with open(model_path) as f:
            model = yaml.safe_load(f)
        glossary = None
        if glossary_path:
            with open(glossary_path) as f:
                glossary = yaml.safe_load(f)
        return cls(model, glossary)


def load_values(con) -> dict[str, list[str]]:
    """Distinct values for the high-cardinality dimensions (what Cortex would use a search service for)."""
    q = {
        "counterparties.counterparty_name": "SELECT DISTINCT counterparty_name FROM dim_counterparty",
        "accounts.account_name": "SELECT DISTINCT account_name FROM dim_account",
        "accounts.desk": "SELECT DISTINCT desk FROM dim_account",
        "securities.ticker": "SELECT DISTINCT ticker FROM dim_security",
        "securities.name_ticker": "SELECT security_name || '|' || ticker FROM dim_security",
        "trades.trader": "SELECT DISTINCT trader FROM fact_trades",
        "settlements.fail_reason": "SELECT DISTINCT fail_reason FROM fact_settlements WHERE fail_reason IS NOT NULL",
    }
    return {k: sorted(r[0] for r in con.execute(sql).fetchall()) for k, sql in q.items()}


def _lit(v) -> str:
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _money(v: float) -> str:
    return f"${v:,.0f}"


class LocalAnalyst:
    def __init__(self, model: SemanticModel, values: dict[str, list[str]], as_of: dt.date = AS_OF):
        self.m = model
        self.as_of = as_of
        self.values = values
        self.entities = self._entity_lexicon(values)

    # ------------------------------------------------------------------ lexicon
    def _entity_lexicon(self, values):
        ents = []  # (phrase, table, column, value(s), topic_only)
        for key in ("counterparties.counterparty_name", "accounts.account_name", "accounts.desk", "trades.trader",
                    "settlements.fail_reason"):
            table, col = key.split(".")
            names = values.get(key, [])
            for v in names:
                ents.append((v.lower(), table, col, v, None))
                ents.append((v.lower().replace("-", " "), table, col, v, None))
            if col == "counterparty_name":
                firsts = {}
                for v in names:
                    w = v.split()[0].lower()
                    firsts.setdefault(w, []).append(v)
                for w, vs in firsts.items():
                    if len(vs) == 1 and len(w) >= 5:
                        ents.append((w, table, col, vs[0], None))
        # company names -> ticker ("apple" -> AAPL), only when the first word is distinctive
        firsts: dict[str, list[str]] = {}
        for pair in values.get("securities.name_ticker", []):
            name, ticker = pair.split("|", 1)
            w = re.split(r"[^a-z]", name.lower())[0]
            firsts.setdefault(w, []).append(ticker)
        for w, tickers in firsts.items():
            if len(tickers) == 1 and len(w) >= 4 and w not in ("core", "global", "bank", "general", "group", "spdr"):
                ents.append((w, "securities", "ticker", tickers[0], None))
        va = self.m.glossary.get("value_aliases", {})
        dim_table = {"asset_class": "securities", "side": "trades", "country": "counterparties", "currency": "*",
                     "break_type": "breaks", "root_cause": "breaks", "owner_team": "breaks", "sector": "securities"}
        for dim, mapping in va.items():
            for value, aliases in mapping.items():
                topic_only = "breaks" if dim in ("root_cause", "owner_team") else None
                v = ["GOVT_BOND", "CORP_BOND"] if (dim == "asset_class" and value == "BOND") else value
                for a in aliases:
                    ents.append((a.lower(), dim_table[dim], dim, v, topic_only))
        for desk in values.get("accounts.desk", []):
            ents.append((desk.lower() + " desk", "accounts", "desk", desk, None))
        ents.sort(key=lambda e: -len(e[0]))
        return ents

    # -------------------------------------------------------------------- parse
    def plan(self, question: str) -> Plan:
        p = Plan(question=question)
        text = " " + re.sub(r"\s+", " ", question.lower().replace("’", "'").replace("?", " ").strip()) + " "
        mask = [False] * len(text)

        def free(span):
            return not any(mask[span[0]:span[1]])

        def take(span):
            for i in range(*span):
                mask[i] = True

        def find(pattern):
            for m in re.finditer(pattern, text):
                if free(m.span()):
                    return m
            return None

        def visible():
            return "".join(" " if mask[i] else ch for i, ch in enumerate(text))

        # 1. dates
        win = parse_window(text, self.as_of)
        if win:
            take(win[3])
            p.window = win[:3]

        # 2. thresholds
        thresholds = []
        for m in AMOUNT.finditer(text):
            if not free(m.span()):
                continue
            num = float(m.group("num").replace(",", ""))
            unit = (m.group("unit") or "").strip()
            if unit.startswith("day") or unit.startswith("business"):
                kind = "days"
            else:
                num *= MULT.get(unit, 1)
                kind = "money" if (m.group("cur") or unit in MULT or m.group("usd") or num >= 1000) else "number"
            thresholds.append((OPS[m.group("op")], num, kind, m.group(0).strip()))
            take(m.span())

        # 3. topic
        t = visible()
        topic = None
        if re.search(r"\bbreaks?\b|\bexceptions?\b", t):
            topic = "breaks"
        elif re.search(r"\bpositions?\b|\bholdings?\b|\bholds?\b|\bheld\b|\bholding\b|market value|\bmv\b|"
                       r"unsettled quantit|traded quantit|settled quantit|traded position|settled position|"
                       r"\bexposure\b", t):
            topic = "positions"
        elif re.search(r"\bfail|\bsettle|\bsettlement|\bpending\b|\bdue\b", t):
            topic = "settlement"
        elif re.search(r"\btrades?\b|\bnotional\b|\bbought\b|\bsold\b|\bbuys?\b|\bsells?\b|\btraded\b|\bbooked\b|"
                       r"\bvolume\b|\btickets?\b|\bdeals?\b|\bexecutions?\b|\btrade size\b|\btrader|\bpurchase|"
                       r"\bcancel", t):
            topic = "trades"
        elif re.search(r"\bcounterpart|\bbrokers?\b", t):
            topic = "counterparties"
        elif re.search(r"\baccounts?\b|\bfunds?\b|\bdesks?\b|\bportfolios?\b", t):
            topic = "accounts"
        elif re.search(r"\bsecurities\b|\btickers?\b|\binstruments?\b", t):
            topic = "securities"
        if topic is None:
            p.warnings.append("Couldn't tell whether this is about trades, settlements, positions or breaks.")
            p.confidence = 0.0
            return p
        p.topic = topic
        p.base = "trades" if topic == "settlement" else topic
        if topic == "settlement":
            p.need.add("settlements")
            p.inner.add("settlements")

        # 4. entity values (longest phrase first, topic-scoped where it matters)
        filters: dict[tuple, list] = {}
        for phrase, table, col, value, topic_only in self.entities:
            if topic_only and topic_only != topic:
                continue
            m = find(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")
            if not m:
                continue
            if col == "currency":
                before = text[max(0, m.start() - 40):m.start()]
                if re.search(r"\bin\s*$", before) and re.search(r"size|notional|value|amount|price|terms", before):
                    take(m.span())  # "size in USD" is a unit, not a filter
                    continue
            take(m.span())
            filters.setdefault((table, col), [])
            vals = value if isinstance(value, list) else [value]
            for v in vals:
                if v not in filters[(table, col)]:
                    filters[(table, col)].append(v)
        # tickers: case-sensitive, from the original question
        for tk in self.values.get("securities.ticker", []):
            if len(tk) < 2 or " " in tk:
                continue
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(tk) + r"(?![A-Za-z0-9])", question):
                filters.setdefault(("securities", "ticker"), [])
                if tk not in filters[("securities", "ticker")]:
                    filters[("securities", "ticker")].append(tk)
                m = find(r"(?<![a-z0-9])" + re.escape(tk.lower()) + r"(?![a-z0-9])")
                if m:
                    take(m.span())
        m = find(r"\btier\s*([123])\b")
        if m:
            filters[("counterparties", "tier")] = [int(m.group(1))]
            take(m.span())

        # 5. superlatives / top-n / which-X
        t = visible()
        order_dir, limit, rank_dim, distinct_dim = None, None, None, None
        m = re.search(r"\btop\s+(\d+)\s+([a-z][a-z \-]*?)(?:\s+by\s+|\s*$|\s+(?=this|last|today|for|in))", t)
        if m:
            limit, order_dir = int(m.group(1)), "DESC"
            rank_dim = self._dim(m.group(2).strip(), p)
            take((m.start(), m.end()))
        m2 = re.search(r"\b(?:which|what)\s+([a-z][a-z \-]*?)\s+(?:has|have|had|is|are|was|booked|did|got|with|"
                       r"shows?|carries|carry)\s+(?:the\s+)?(most|highest|largest|biggest|greatest|lowest|fewest|"
                       r"least|smallest)\b", t)
        if m2 and not rank_dim:
            rank_dim = self._dim(m2.group(1).strip(), p)
            order_dir = "ASC" if m2.group(2) in ("lowest", "fewest", "least", "smallest") else "DESC"
            limit = 1
            take(m2.span(1))
        m3 = re.search(r"^\s*(?:which|what)\s+([a-z][a-z \-]*?)\s+(?:have|has|hold|holds|are|is|had|traded|with|"
                       r"show|carry|own)\b", t)
        if m3 and not rank_dim:
            word = m3.group(1).strip()
            if not re.fullmatch(BASE_ENTITY.get(p.base, "$^") + r"|" + BASE_ENTITY.get(topic, "$^"), word):
                d = self._dim(word, p)
                if d:
                    distinct_dim = d
                    take(m3.span(1))

        # 6. group by
        t = visible()
        group_dims = []
        for gm in re.finditer(r"\b(?:by|per|for each|for every|across|broken down by|split by|grouped by)\s+"
                              r"([a-z][a-z \-]*)", t):
            words = gm.group(1).split()
            for n in (3, 2, 1):
                phrase = " ".join(words[:n])
                d = self._dim(phrase, p)
                if d:
                    group_dims.append(d)
                    s0 = gm.start()
                    take((s0, gm.start(1) + len(phrase)))
                    break
        if re.search(r"\bdaily\b|\beach day\b|\bper day\b|\bby day\b", t):
            d = self._dim("day", p)
            if d and d not in group_dims:
                group_dims.append(d)

        # 7. status filters from the semantic model + topic rules
        t = visible()
        wants_rate = bool(re.search(r"\bpercent|percentage|\brate\b|\bratio\b|%", t))
        rate_cond = None
        if topic == "settlement":
            if re.search(r"on time", t):
                cond = "s.settlement_status = 'SETTLED' AND s.actual_settle_date = s.expected_settle_date"
                if wants_rate:
                    rate_cond = (cond, "settled on time")
                else:
                    p.add_where(cond, "Settled on time: actual settlement date equals the expected date")
            elif re.search(r"\bfail", t):
                cond = "s.settlement_status = 'FAILED'"
                if wants_rate:
                    rate_cond = (cond, "failed")
                else:
                    p.add_where(cond, "Failed: past the expected settlement date and still not settled")
            elif re.search(r"\bpending\b|awaiting|not yet settled|\bunsettled\b", t):
                p.add_where("s.settlement_status = 'PENDING'", "Pending: booked, not yet due to settle")
            elif re.search(r"\bsettled\b", t):
                p.add_where("s.settlement_status = 'SETTLED'", "Settled")
        if topic == "breaks":
            if re.search(r"\b(?:aged|stale)\b", t):
                p.add_where("b.break_status IN ('OPEN', 'INVESTIGATING')", "Open breaks (OPEN or INVESTIGATING)")
                if not any(k == "days" for _, _, k, _ in thresholds):
                    p.add_where("b.age_days > 5", "Aged: open more than 5 business days")
            elif re.search(r"\bresolved\b|\bclosed\b|\bcleared\b", t):
                p.add_where("b.break_status = 'RESOLVED'", "Resolved breaks")
            elif re.search(r"\bopen\b|\boutstanding\b|\bunresolved\b|\blive\b|\boldest\b", t):
                p.add_where("b.break_status IN ('OPEN', 'INVESTIGATING')", "Open breaks (OPEN or INVESTIGATING)")
            elif re.search(r"\binvestigating\b", t):
                p.add_where("b.break_status = 'INVESTIGATING'", "Under investigation")
        if p.base == "trades":
            if re.search(r"\bcancel", t):
                p.add_where("t.trade_status = 'CANCELLED'", "Cancelled trades only")
            else:
                p.add_where("t.trade_status <> 'CANCELLED'", "Cancelled trades excluded (active-trades rule in the glossary)")

        # 8. entity filters -> WHERE
        for (table, col), vals in filters.items():
            table, col = self._resolve(table, col, p)
            if table is None:
                continue
            ref = self._ref(table, col, p)
            if len(vals) == 1:
                p.add_where(f"{ref} = {_lit(vals[0])}", f"{col.replace('_', ' ')} = {vals[0]}")
            else:
                p.add_where(f"{ref} IN ({', '.join(_lit(v) for v in vals)})",
                            f"{col.replace('_', ' ')} in {', '.join(map(str, vals))}")

        # 9. thresholds -> WHERE
        for op, num, kind, raw in thresholds:
            if kind == "days":
                col = ("breaks", "age_days") if p.base == "breaks" else (
                    ("settlements", "days_failing") if topic == "settlement" else None)
                if col is None:
                    p.warnings.append(f"Ignored '{raw}': no day-count on this data")
                    continue
                p.add_where(f"{self._ref(*col, p)} {op} {int(num)}", f"{col[1]} {op} {int(num)} business days")
                if p.base == "breaks" and not any("break_status" in w for w in p.where):
                    p.add_where("b.break_status IN ('OPEN', 'INVESTIGATING')", "Open breaks (OPEN or INVESTIGATING)")
            else:
                col = {"trades": ("trades", "notional_usd"), "breaks": ("breaks", "amount_usd"),
                       "positions": ("positions", "market_value_usd")}.get(p.base)
                if col is None:
                    p.warnings.append(f"Ignored '{raw}': no amount on this data")
                    continue
                p.add_where(f"{self._ref(*col, p)} {op} {int(num)}", f"{col[1]} {op} {_money(num)}")

        # 10. time window
        if p.window:
            start, end, label = p.window
            tcol = self._time_col(topic, t, p)
            if tcol:
                ref = self._ref(*tcol, p)
                if start == end:
                    p.add_where(f"{ref} = '{start}'", f"{label}: {tcol[1]} = {start}")
                else:
                    p.add_where(f"{ref} BETWEEN '{start}' AND '{end}'", f"{label}: {tcol[1]} {start} to {end}")
                if topic == "settlement":
                    p.explain.append("Settlement questions filter on the expected settlement date (glossary)")
        if p.base == "positions" and not any("as_of_date" in w for w in p.where):
            p.add_where(f"p.as_of_date = '{self.as_of}'", f"Latest position snapshot ({self.as_of})")

        # 11. positions semantics
        if p.base == "positions":
            if re.search(r"\bhold\b|\bholds\b|\bheld\b|\bholding\b(?! period)", t):
                p.add_where("p.traded_quantity > 0", "Hold = long traded position")
            if re.search(r"unsettled|different from settled|differs? from settled|traded (?:vs|versus) settled "
                         r"(?:differ|mismatch)|not (?:yet )?settled|mismatch", t):
                p.add_where("p.traded_quantity <> p.settled_quantity", "Traded and settled quantity differ")

        # 12. measures + metric
        t = visible()
        measures = self._measures(t, p, topic)
        agg = None
        if wants_rate and rate_cond:
            agg = "RATE"
        elif re.search(r"\bhow many\b|\bnumber of\b|\bcount\b|\bcount of\b", t):
            agg = "COUNT"
        elif re.search(r"\baverage\b|\bavg\b|\bmean\b", t):
            agg = "AVG"
        elif re.search(r"\btotal\b|\bsum\b|\bhow much\b|\bcompare\b|\boverall\b", t):
            agg = "SUM"
        listing_words = bool(re.search(r"^\s*(?:list|show|display|give me|get|find|pull)\b", t)) or \
            bool(re.search(r"^\s*(?:which|what)\s+(?:are the\s+)?(?:" + BASE_ENTITY.get(p.base, "$^") + r")\b", t))
        superl = re.search(r"\b(?:(\d+)\s+)?(largest|biggest|smallest|oldest|newest|latest|most recent)\b", t)

        count_distinct = None
        md = re.search(r"\bdistinct\s+([a-z][a-z ]*?)\b(?:did|do|were|are|have|in|traded|this|last|$)", t)
        if md and agg == "COUNT":
            d = self._dim(md.group(1).strip(), p)
            if d:
                count_distinct = d

        dims = group_dims[:]
        if rank_dim and rank_dim not in dims:
            dims.insert(0, rank_dim)
        if distinct_dim and not dims and agg is None:
            col_ref = self._ref(*distinct_dim, p)
            p.select = [(col_ref, distinct_dim[1])]
            p.distinct = True
            p.explain.append(f"Distinct {distinct_dim[1].replace('_', ' ')} values")
        elif dims or agg or measures or rank_dim:
            for d in dims:
                ref = self._ref(*d, p)
                p.select.append((ref, d[1]))
                p.group.append(ref)
            if dims:
                p.explain.append("Grouped by " + ", ".join(d[1].replace("_", " ") for d in dims))
            metric_exprs = []
            if agg == "RATE":
                cond, label = rate_cond
                metric_exprs.append((f"100.0 * SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) / COUNT(*)",
                                     f"pct_{label.replace(' ', '_')}", f"Percentage {label}"))
            elif agg == "COUNT" or (not measures and agg is None):
                if count_distinct:
                    ref = self._ref(*count_distinct, p) if count_distinct[1] != "ticker" else \
                        f"{ALIAS[p.base]}.security_id"
                    metric_exprs.append((f"COUNT(DISTINCT {ref})", f"distinct_{count_distinct[1]}",
                                         f"Counted distinct {count_distinct[1].replace('_', ' ')}"))
                else:
                    noun = {"trades": "trades", "settlement": "trades", "breaks": "breaks", "positions": "positions",
                            "counterparties": "counterparties", "accounts": "accounts",
                            "securities": "securities"}[topic]
                    if p.base == "positions":
                        p.add_where("p.traded_quantity <> 0", "A position = non-zero traded quantity")
                    metric_exprs.append(("COUNT(*)", f"{noun}_count", f"Counted {noun}"))
            else:
                fn = {"AVG": "AVG", "SUM": "SUM", None: "SUM", "COUNT": "COUNT"}[agg]
                for expr, label in measures:
                    alias = f"{'avg' if fn == 'AVG' else 'total'}_{label}"
                    metric_exprs.append((f"{fn}({expr})", alias,
                                         f"{'Average' if fn == 'AVG' else 'Summed'} {label.replace('_', ' ')}"))
            for expr, alias, why in metric_exprs:
                p.select.append((expr, alias))
                p.explain.append(why)
            if order_dir:
                p.order.append(f"{metric_exprs[0][1]} {order_dir}")
                p.limit = limit
                p.explain.append(f"{'Top' if order_dir == 'DESC' else 'Bottom'} {limit} by {metric_exprs[0][1]}")
            elif dims and dims[0][1] in ("trade_date", "as_of_date", "opened_date", "resolved_date",
                                         "expected_settle_date"):
                p.order.append(f"{dims[0][1]}")
            elif re.search(r"highest first|descending|largest first|biggest first|sorted|ranked", t) or dims:
                p.order.append(f"{metric_exprs[0][1]} DESC")
        else:
            listing_words = True
        if not p.select:
            cols = LIST_COLS.get(topic, LIST_COLS.get(p.base, []))
            for table, col in cols:
                table, col = self._resolve(table, col, p)
                p.select.append((self._ref(table, col, p), col))
            p.explain.append("Listed matching rows")
            if superl:
                n = int(superl.group(1)) if superl.group(1) else (limit or 1)
                word = superl.group(2)
                if word in ("oldest",):
                    key = "b.age_days DESC" if p.base == "breaks" else f"{self._ref(*self._time_col(topic, t, p), p)} ASC"
                elif word in ("newest", "latest", "most recent"):
                    key = f"{self._ref(*self._time_col(topic, t, p), p)} DESC"
                else:
                    amt = {"trades": "t.notional_usd", "breaks": "b.amount_usd", "positions": "p.market_value_usd"}
                    key = amt.get(p.base, "1") + (" ASC" if word == "smallest" else " DESC")
                p.order.append(key)
                p.limit = n
                p.explain.append(f"{word.capitalize()} {n}")
        p.sql = self._compose(p)
        unknown = [w for w in re.findall(r"[a-z][a-z\-']+", visible())
                   if w not in STOP and not self._known_word(w)]
        if unknown:
            p.warnings.append("Didn't use: " + ", ".join(sorted(set(unknown))))
        p.confidence = max(0.3, 1.0 - 0.12 * len(set(unknown)))
        return p

    # ------------------------------------------------------------------ helpers
    KNOWN = set("""trades trade notional failed fail fails failing settle settled settlement settlements pending breaks
    break positions position holdings holding market value mv quantity traded unsettled buy buys bought sell sells sold
    booked trader traders cancelled canceled cancel open resolved aged stale amount size usd price prices days day
    daily counterparty counterparties account accounts desk desks security securities ticker tickers currency
    currencies tier side reason reasons owner team teams type cause root due time percentage percent rate on
    largest biggest oldest top base-currency base settled distinct different exposure denominated positions
    hold holds held book books executions deals tickets volume quantities age average total equities bonds""".split())

    def _known_word(self, w: str) -> bool:
        if w in self.KNOWN:
            return True
        return any(w in phrase.split() for phrase, *_ in DIM_WORDS)

    def _dim(self, phrase: str, p: Plan):
        phrase = phrase.strip().lower()
        for word, (table, col) in DIM_WORDS:
            if phrase == word:
                return self._resolve(table, col, p)
        return None

    def _resolve(self, table, col, p: Plan):
        base = p.base
        if table == "*" and col == "currency":
            return ("positions", "currency") if base == "positions" else ("trades", "trade_ccy")
        if table == "*" and col == "status":
            return {"breaks": ("breaks", "break_status"), "trades": ("settlements", "settlement_status")
                    if p.topic == "settlement" else ("trades", "trade_status")}.get(base, (None, None))
        if table == "*" and col == "date":
            return self._time_col(p.topic, "", p)
        if table == "counterparties" and base in ("positions", "breaks"):
            return None, None
        if table == "settlements" and base != "trades":
            return None, None
        if table == "trades" and base not in ("trades",):
            return None, None
        if table == "breaks" and base != "breaks":
            return None, None
        return table, col

    def _time_col(self, topic, t, p: Plan):
        if topic == "breaks":
            if re.search(r"\bresolved\b|\bclosed\b|\bcleared\b", t or " ".join(p.where)):
                return ("breaks", "resolved_date")
            return ("breaks", "opened_date")
        if topic in TIME_COL:
            return TIME_COL[topic]
        return None

    def _ref(self, table, col, p: Plan) -> str:
        if table != p.base:
            p.need.add(table)
        return f"{ALIAS[table]}.{col}"

    def _measures(self, t: str, p: Plan, topic: str):
        out = []
        if p.base == "positions":
            if re.search(r"\btraded\b", t) and re.search(r"\bsettled\b", t) and re.search(r"market value|\bmv\b", t):
                return [("p.market_value_usd", "market_value_usd"),
                        ("p.settled_market_value_usd", "settled_market_value_usd")]
            if re.search(r"settled market value|settled mv", t):
                out.append(("p.settled_market_value_usd", "settled_market_value_usd"))
            elif re.search(r"local market value", t):
                out.append(("p.market_value_local", "market_value_local"))
            elif re.search(r"market value|\bmv\b|exposure|\bvalue\b", t):
                out.append(("p.market_value_usd", "market_value_usd"))
            elif re.search(r"unsettled quantit|unsettled position", t):
                out.append(("p.traded_quantity - p.settled_quantity", "unsettled_quantity"))
            elif re.search(r"settled quantit|settled position", t):
                out.append(("p.settled_quantity", "settled_quantity"))
            elif re.search(r"quantit", t):
                out.append(("p.traded_quantity", "traded_quantity"))
        elif p.base == "breaks":
            if re.search(r"\bage\b|days open|how old", t):
                out.append(("b.age_days", "age_days"))
            elif re.search(r"\bamount\b|\bvalue\b|\bexposure\b|\busd\b|\bsize\b|\$", t):
                out.append(("b.amount_usd", "amount_usd"))
        elif p.base == "trades":
            if topic == "settlement" and re.search(r"days failing|fail age|days failed|days outstanding", t):
                out.append(("s.days_failing", "days_failing"))
            elif re.search(r"\bprice\b", t):
                out.append(("t.price", "price"))
            elif re.search(r"\bnotional\b|trade size|\bsize\b|\bvalue\b|\bvolume\b|\bamount\b|how much|\busd\b", t):
                out.append(("t.notional_usd", "notional_usd"))
            elif re.search(r"\bquantity\b|\bshares\b|\bunits\b", t):
                out.append(("t.quantity", "quantity"))
        return out

    def _compose(self, p: Plan) -> str:
        base_alias = ALIAS[p.base]
        sql = "SELECT " + ("DISTINCT " if p.distinct else "")
        sql += ", ".join(expr if expr.endswith(f".{alias}") else f"{expr} AS {alias}" for expr, alias in p.select)
        sql += f"\nFROM {self.m.physical[p.base]} {base_alias}"
        for table in sorted(p.need, key=lambda x: list(ALIAS).index(x)):
            rel = self.m.rel.get((p.base, table))
            if not rel:
                p.warnings.append(f"No relationship from {p.base} to {table}")
                continue
            lc, rc, jt = rel
            kind = "JOIN" if (jt == "inner" or table in p.inner) else "LEFT JOIN"
            sql += f"\n{kind} {self.m.physical[table]} {ALIAS[table]} ON {ALIAS[table]}.{rc} = {base_alias}.{lc}"
        if p.where:
            sql += "\nWHERE " + "\n  AND ".join(p.where)
        if p.group:
            sql += "\nGROUP BY " + ", ".join(p.group)
        if p.order:
            sql += "\nORDER BY " + ", ".join(p.order)
        if p.limit:
            sql += f"\nLIMIT {p.limit}"
        return sql

    def explain(self, p: Plan) -> str:
        lines = [f"- {e}" for e in p.explain]
        return "\n".join(lines)
