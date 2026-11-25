"""Build the data + Python bundle for the GitHub Pages demo.

    python -m copilot.export_site

docs/data/   SQLite copy, semantic model + glossary as JSON, precomputed answers, benchmark
docs/py/     this package zipped for Pyodide (the sqlglot wheel sits next to it)
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile

import yaml

from .benchmark import load_set, run
from .config import GLOSSARY, LOCAL_DB, ROOT, SEMANTIC_MODEL
from .executor import to_sqlite
from .local_analyst import load_values
from .pipeline import Copilot

DOCS = ROOT / "docs"
BROWSER_MODULES = ["__init__.py", "config.py", "guard.py", "executor.py", "local_analyst.py", "web.py"]


def _dump(name, obj):
    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    (DOCS / "data" / name).write_text(json.dumps(obj, separators=(",", ":"), default=str))


def main() -> None:
    model = yaml.safe_load(open(SEMANTIC_MODEL))
    glossary = yaml.safe_load(open(GLOSSARY))
    with sqlite3.connect(LOCAL_DB) as con:
        values = load_values(con)
        schema = []
        for t in model["tables"]:
            phys = t["base_table"]["table"].lower()
            n = con.execute(f"SELECT COUNT(*) FROM {phys}").fetchone()[0]
            schema.append(dict(name=t["name"], table=t["base_table"]["table"], rows=n))
    _dump("model.json", model)
    _dump("glossary.json", glossary)
    _dump("values.json", values)
    _dump("schema.json", schema)
    shutil.copy(LOCAL_DB, DOCS / "data" / "trade_ops.db")

    copilot = Copilot(engine="local")
    answers = {}
    questions = [x["question"] for x in load_set("gold")] + [x["question"] for x in load_set("dev")]
    questions += [v["question"] for v in model.get("verified_queries", [])]
    for q in dict.fromkeys(questions):
        a = copilot.ask(q).as_dict()
        a["rows"] = a["rows"][:60]
        a["sqlite"] = to_sqlite(a["guard"]["sql"]) if a.get("guard", {}).get("ok") else None
        answers[q] = a
    _dump("answers.json", answers)

    m = run("gold", "local", copilot=copilot)
    for r in m["results"]:
        r.pop("warnings", None)
    _dump("benchmark.json", m)

    (DOCS / "py").mkdir(exist_ok=True)
    with zipfile.ZipFile(DOCS / "py" / "copilot.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in BROWSER_MODULES:
            z.write(ROOT / "copilot" / f, f"copilot/{f}")
    size = sum(p.stat().st_size for p in (DOCS / "data").glob("*")) / 1024
    print(f"docs/data: {size:.0f} KB · {len(answers)} precomputed answers · gold {m['passed']}/{m['n']}")


if __name__ == "__main__":
    main()
