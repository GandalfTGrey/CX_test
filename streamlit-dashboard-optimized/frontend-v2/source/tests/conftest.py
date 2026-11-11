from pathlib import Path
import importlib.util

import pytest


@pytest.fixture(scope='session')
def evaluation_fixture(tmp_path_factory):
    """Only tests can create synthetic evaluations; the app has no such entry."""
    root = tmp_path_factory.mktemp('synthetic-evaluations')
    spec = importlib.util.spec_from_file_location('evaluation_fixture_factory', Path(__file__).parent / 'fixture_factory.py')
    factory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(factory)
    factory.ROOT = root
    factory.main()
    return root


@pytest.fixture(autouse=True)
def configured_root(monkeypatch, evaluation_fixture):
    monkeypatch.setenv('DASHBOARD_DATA_ROOT', str(evaluation_fixture))
