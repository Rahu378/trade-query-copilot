"""Cortex Analyst client.

Two transports:
  * REST from anywhere: POST {account_url}/api/v2/cortex/analyst/message with a
    programmatic access token (or key-pair JWT / OAuth token),
  * inside Streamlit in Snowflake: _snowflake.send_snow_api_request, no token needed.

Multi-turn works by sending the running message list back each time. Analyst
turns keep role "analyst" with the content blocks Cortex returned.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import SEMANTIC_MODEL_STAGE, SNOWFLAKE_ACCOUNT_URL, SNOWFLAKE_TOKEN, SNOWFLAKE_TOKEN_TYPE

API_PATH = "/api/v2/cortex/analyst/message"


@dataclass
class AnalystReply:
    text: str = ""
    sql: str | None = None
    suggestions: list = field(default_factory=list)
    confidence: float | None = None
    verified_query: str | None = None
    request_id: str | None = None
    warnings: list = field(default_factory=list)
    history: list = field(default_factory=list)


def parse_response(payload: dict, history: list) -> AnalystReply:
    msg = payload.get("message", {})
    reply = AnalystReply(request_id=payload.get("request_id"),
                         warnings=[w.get("message", str(w)) for w in payload.get("warnings", [])])
    texts = []
    for block in msg.get("content", []):
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text", ""))
        elif kind == "sql":
            reply.sql = block.get("statement")
            conf = block.get("confidence") or {}
            vq = conf.get("verified_query_used")
            if vq:
                reply.verified_query = vq.get("name") or vq.get("question")
        elif kind == "suggestions":
            reply.suggestions = block.get("suggestions", [])
    reply.text = "\n\n".join(t for t in texts if t)
    reply.history = history + [{"role": "analyst", "content": msg.get("content", [])}]
    return reply


class CortexAnalyst:
    def __init__(self, account_url: str = SNOWFLAKE_ACCOUNT_URL, token: str = SNOWFLAKE_TOKEN,
                 token_type: str = SNOWFLAKE_TOKEN_TYPE, semantic_model_file: str = SEMANTIC_MODEL_STAGE,
                 timeout: int = 60, session=None):
        self.account_url = account_url.rstrip("/")
        self.token = token
        self.token_type = token_type
        self.semantic_model_file = semantic_model_file
        self.timeout = timeout
        self._in_snowflake = False
        try:  # Streamlit in Snowflake
            import _snowflake  # type: ignore  # noqa: F401

            self._in_snowflake = True
        except ImportError:
            if not (self.account_url and self.token):
                raise RuntimeError("set SNOWFLAKE_ACCOUNT_URL and SNOWFLAKE_TOKEN to use Cortex Analyst") from None

    def ask(self, question: str, history: list | None = None) -> AnalystReply:
        messages = list(history or []) + [{"role": "user", "content": [{"type": "text", "text": question}]}]
        body = {"messages": messages, "semantic_model_file": self.semantic_model_file, "stream": False}
        if self._in_snowflake:
            import _snowflake  # type: ignore

            resp = _snowflake.send_snow_api_request("POST", API_PATH, {}, {}, body, None, self.timeout * 1000)
            status = resp.get("status")
            payload = json.loads(resp.get("content") or "{}")
            if status is None or status >= 400:
                raise RuntimeError(f"Cortex Analyst {status}: {payload.get('message', payload)}")
        else:
            import requests

            r = requests.post(
                self.account_url + API_PATH,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Snowflake-Authorization-Token-Type": self.token_type,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Cortex Analyst {r.status_code}: {r.text[:300]}")
            payload = r.json()
        return parse_response(payload, messages)
