from __future__ import annotations

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401  (registers tables)


def make_engine(url: str):
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):  # WAL for concurrent reads, FKs on
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    SQLModel.metadata.create_all(engine)
    return engine


def new_session(engine) -> Session:
    return Session(engine, expire_on_commit=False)
