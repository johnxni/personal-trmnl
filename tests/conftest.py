"""Shared pytest fixtures for the test suite."""
from pathlib import Path

import pytest

EDGE_CASES_DIR = Path(__file__).parent / 'fixtures' / 'edge_cases'


@pytest.fixture
def load_edge_case():
    """
    Return a loader that reads an edge-case fixture file by name.

    Returns
    -------
    callable
        Function ``load(name)`` returning the file contents as ``str``.
    """
    def _load(name: str) -> str:
        return (EDGE_CASES_DIR / name).read_text()
    return _load
