import pytest


@pytest.fixture(autouse=True)
def _fixed_hash_seed(monkeypatch):
    """Keep `cli.main` in-process: without a fixed seed it relaunches itself in a
    fresh interpreter (see cli._relaunch_with_fixed_hash_seed), which is tested
    on its own through a real subprocess."""
    monkeypatch.setenv("PYTHONHASHSEED", "0")


@pytest.fixture(autouse=True)
def _isolated_modules(tmp_path_factory):
    """Traced scripts import their siblings in-process; a `helper` module cached
    by one test must not answer another test's `import helper`. Only modules
    loaded from test temp dirs are dropped: evicting stdlib ones is unsafe."""
    import sys
    from pathlib import Path

    base = Path(tmp_path_factory.getbasetemp()).resolve()
    yield
    for name, mod in list(sys.modules.items()):
        file = getattr(mod, "__file__", None)
        if file and Path(file).resolve().is_relative_to(base):
            del sys.modules[name]
