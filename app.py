"""Trade Query Copilot, the Streamlit front end.

    streamlit run app.py

Runs three ways:
  * locally against data/trade_ops.db with the rule-based engine (no accounts needed),
  * locally against Snowflake + Cortex Analyst (set SNOWFLAKE_ACCOUNT_URL, SNOWFLAKE_TOKEN and the
    connector settings, see README),
  * as Streamlit in Snowflake, where Cortex and the session come from the environment.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st
import yaml

from copilot.config import AS_OF, GLOSSARY, ROOT
from copilot.pipeline import Copilot

st.set_page_config(page_title="Trade Query Copilot", layout="wide")


def _in_snowflake() -> bool:
    try:
        import _snowflake  # type: ignore  # noqa: F401

        return True
    except ImportError:
        return False


@st.cache_resource
def get_copilot(engine: str) -> Copilot:
    if engine == "cortex":
        from copilot.executor import SnowflakeExecutor

        if _in_snowflake():
            from snowflake.snowpark.context import get_active_session

            return Copilot(engine="cortex", executor=SnowflakeExecutor(session=get_active_session()))
        return Copilot(engine="cortex", executor=SnowflakeExecutor(
            account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
            authenticator=os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")))
    return Copilot(engine="local")


@st.cache_data
def glossary() -> dict:
    with open(GLOSSARY) as f:
        return yaml.safe_load(f)


@st.cache_data
def suggestions() -> list[str]:
    with open(ROOT / "eval" / "dev.yaml") as f:
        dev = [x["question"] for x in yaml.safe_load(f)]
    return ["Which trades over $1M failed to settle this week, by counterparty?",
            "Traded vs settled market value by account", "Open breaks by owner team"] + dev[:6]


cortex_ready = _in_snowflake() or bool(os.getenv("SNOWFLAKE_ACCOUNT_URL") and os.getenv("SNOWFLAKE_TOKEN"))

with st.sidebar:
    st.header("Trade Query Copilot")
    st.caption(f"Snapshot {AS_OF:%a %d %b %Y}")
    engine = st.radio("Engine", ["local", "cortex"], index=1 if _in_snowflake() else 0,
                      format_func=lambda e: "Local engine (SQLite)" if e == "local" else "Cortex Analyst (Snowflake)",
                      disabled=not cortex_ready)
    if not cortex_ready:
        st.caption("Cortex needs SNOWFLAKE_ACCOUNT_URL + SNOWFLAKE_TOKEN, or Streamlit in Snowflake.")
    st.subheader("Glossary")
    for term in glossary()["terms"]:
        with st.expander(term["term"]):
            st.write(term["definition"])
            if term.get("maps_to"):
                st.code(term["maps_to"], language="sql")
            if term.get("rule"):
                st.caption(term["rule"])
    if st.button("Clear chat"):
        st.session_state.pop("chat", None)
        get_copilot(engine).history = []

copilot = get_copilot(engine)
chat = st.session_state.setdefault("chat", [])


def render(ans: dict) -> None:
    if ans.get("explanation"):
        st.markdown("\n".join(f"- {e}" for e in ans["explanation"]))
    for w in ans.get("warnings") or []:
        st.caption(f"⚠ {w}")
    if ans.get("guard"):
        checks = ans["guard"]["checks"]
        st.caption(" · ".join(("✓ " if c["ok"] else "✗ ") + c["name"] + (f" ({c['detail']})" if c["detail"] else "")
                              for c in checks))
    with st.expander("SQL", expanded=False):
        st.code(ans["guard"]["sql"] if ans.get("guard") else ans.get("sql", ""), language="sql")
    if ans.get("error"):
        st.error(ans["error"])
        return
    df = pd.DataFrame(ans["rows"], columns=ans["columns"])
    if len(df) == 1 and len(df.columns) == 1:
        st.metric(df.columns[0].replace("_", " "), f"{df.iloc[0, 0]:,.2f}" if isinstance(df.iloc[0, 0], float)
                  else f"{df.iloc[0, 0]:,}" if isinstance(df.iloc[0, 0], int) else df.iloc[0, 0])
    else:
        st.dataframe(df, hide_index=True, width="stretch")
        num = [c for c in df.columns[1:] if pd.api.types.is_numeric_dtype(df[c])]
        if 1 < len(df) <= 40 and num and not pd.api.types.is_numeric_dtype(df[df.columns[0]]):
            st.bar_chart(df.set_index(df.columns[0])[num[0]])
    st.caption(f"{len(df)} rows · {ans['elapsed_ms']} ms · {ans['engine']}"
               + (f" · confidence {ans['confidence']:.0%}" if ans.get("confidence") is not None else ""))


st.title("Ask the trade warehouse")
if not chat:
    cols = st.columns(3)
    for i, s in enumerate(suggestions()):
        if cols[i % 3].button(s, key=f"s{i}", width="stretch"):
            chat.append({"role": "user", "content": s})
            chat.append({"role": "assistant", "answer": copilot.ask(s).as_dict()})
            st.rerun()

for m in chat:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.write(m["content"])
        else:
            render(m["answer"])

if q := st.chat_input("e.g. Which trades over $1M failed to settle this week, by counterparty?"):
    chat.append({"role": "user", "content": q})
    chat.append({"role": "assistant", "answer": copilot.ask(q).as_dict()})
    st.rerun()
