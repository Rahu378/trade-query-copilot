import datetime as dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("COPILOT_DATA_DIR", ROOT / "data"))
LOCAL_DB = Path(os.getenv("COPILOT_LOCAL_DB", DATA_DIR / "trade_ops.db"))
SEMANTIC_MODEL = ROOT / "semantic_model" / "trade_ops.yaml"
GLOSSARY = ROOT / "semantic_model" / "glossary.yaml"

# the dataset is a snapshot; "today" means this date everywhere (semantic model says the same)
AS_OF = dt.date(2026, 11, 20)

DATABASE = "TRADE_OPS"
SCHEMA = "CORE"
MAX_ROWS = 1000

# Snowflake / Cortex Analyst
SNOWFLAKE_ACCOUNT_URL = os.getenv("SNOWFLAKE_ACCOUNT_URL", "")  # https://<org>-<account>.snowflakecomputing.com
SNOWFLAKE_TOKEN = os.getenv("SNOWFLAKE_TOKEN", "")              # programmatic access token or key-pair JWT
SNOWFLAKE_TOKEN_TYPE = os.getenv("SNOWFLAKE_TOKEN_TYPE", "PROGRAMMATIC_ACCESS_TOKEN")
SEMANTIC_MODEL_STAGE = os.getenv("COPILOT_SEMANTIC_MODEL_FILE", "@TRADE_OPS.CORE.SEMANTIC_MODELS/trade_ops.yaml")
SNOWFLAKE_ROLE = os.getenv("SNOWFLAKE_ROLE", "COPILOT_READER")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "COPILOT_WH")
