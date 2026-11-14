"""Initialise every lazily-built singleton once, before worker threads start (catalog, DB server, embeddings)."""
from app.db.connection import owner_dsn
from app.nl2sql.catalog import get_catalog
from app.nl2sql import examples


def warm() -> None:
    owner_dsn()
    get_catalog()
    examples.top_k("warm up", 1)
