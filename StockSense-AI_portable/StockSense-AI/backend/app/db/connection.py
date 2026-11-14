"""Database connections.

Uses DATABASE_URL if set; otherwise an embedded PostgreSQL (pgserver) stored in backend/.pgdata.
The chatbot executes SQL only through the restricted `chatbot_ro` role.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

import psycopg2

from app import config

RO_ROLE = "chatbot_ro"
_server = None
_lock = threading.Lock()


def owner_dsn() -> str:
    global _server
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    with _lock:  # server start-up is not thread-safe
        if _server is None:
            import pgserver

            config.PGDATA_DIR.mkdir(parents=True, exist_ok=True)
            _server = pgserver.get_server(str(config.PGDATA_DIR), cleanup_mode=None)  # keep running after exit
        return _server.get_uri()


def readonly_dsn() -> str:
    """Same server, restricted role that only has SELECT on the retail tables/views."""
    if os.getenv("READONLY_DATABASE_URL"):
        return os.environ["READONLY_DATABASE_URL"]
    u = urlparse(owner_dsn())
    netloc = f"{RO_ROLE}:chatbot_ro_pw@{u.hostname}" + (f":{u.port}" if u.port else "")
    return urlunparse(u._replace(netloc=netloc))


@contextmanager
def owner_conn():
    conn = psycopg2.connect(owner_dsn())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
