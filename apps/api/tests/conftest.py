import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from pulso.config import get_settings

API_DIR = Path(__file__).resolve().parents[1]


def _url_do_banco_de_teste() -> URL:
    settings = get_settings()
    dev = make_url(settings.database_url)
    if settings.test_database_url:
        teste = make_url(settings.test_database_url)
    else:
        teste = dev.set(database=f"{dev.database}_test")
    if teste.database == dev.database and teste.host == dev.host and teste.port == dev.port:
        raise RuntimeError("banco de teste igual ao de desenvolvimento; recusando apagar o schema")
    return teste


# Aponta TUDO (SessionLocal do app, env.py do Alembic, fixtures) para o banco de teste antes de
# qualquer modulo importar pulso.db. O banco de desenvolvimento nunca e tocado pela suite.
_TEST_URL = _url_do_banco_de_teste()
os.environ["DATABASE_URL"] = _TEST_URL.render_as_string(hide_password=False)
get_settings.cache_clear()


def _recriar_schema_via_migrations() -> None:
    admin = create_engine(_TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        existe = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": _TEST_URL.database}
        ).scalar()
        if not existe:
            conn.execute(text(f'CREATE DATABASE "{_TEST_URL.database}"'))
    admin.dispose()

    limpo = create_engine(_TEST_URL, isolation_level="AUTOCOMMIT")
    with limpo.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    limpo.dispose()

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    _recriar_schema_via_migrations()
    engine = create_engine(_TEST_URL, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session, None, None]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
