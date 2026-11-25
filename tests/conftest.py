import sqlite3

import pytest

from copilot import seed
from copilot.config import GLOSSARY, SEMANTIC_MODEL
from copilot.executor import LocalExecutor
from copilot.local_analyst import LocalAnalyst, SemanticModel, load_values
from copilot.pipeline import Copilot


@pytest.fixture(scope="session")
def tables():
    return seed.build(11)


@pytest.fixture(scope="session")
def db_path(tables, tmp_path_factory):
    p = tmp_path_factory.mktemp("w") / "trade_ops.db"
    seed.write_sqlite(tables, p)
    return p


@pytest.fixture(scope="session")
def analyst(db_path):
    model = SemanticModel.load(SEMANTIC_MODEL, GLOSSARY)
    with sqlite3.connect(db_path) as con:
        return LocalAnalyst(model, load_values(con))


@pytest.fixture(scope="session")
def executor(db_path):
    return LocalExecutor(db_path)


@pytest.fixture(scope="session")
def copilot(analyst, executor):
    return Copilot(engine="local", executor=executor, analyst=analyst)
