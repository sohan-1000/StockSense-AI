"""Every few-shot example must pass the validator and execute against the real database."""
import pytest

from app.nl2sql.catalog import get_catalog
from app.nl2sql.examples import _load
from app.nl2sql.executor import execute
from app.nl2sql.schema_builder import build
from app.nl2sql.validator import validate

FULL = build(get_catalog(), [], full=True)


@pytest.mark.parametrize("ex", _load(), ids=[e["q"][:50] for e in _load()])
def test_example_is_valid_and_runs(ex):
    r = execute(validate(ex["sql"], FULL.allowed))
    assert r.columns
