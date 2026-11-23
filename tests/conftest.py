import json
import pytest
from jacq import catalog as C


@pytest.fixture
def dummy_vi(tmp_path):
    p = tmp_path / "dummy.vi"
    p.write_bytes(b"")
    return str(p)


@pytest.fixture
def tmp_queue(tmp_path, monkeypatch):
    """--offline writes the probe queue: in tests it is redirected to a temporary file, so catalog/probe_queue.json isn't polluted"""
    q = tmp_path / "probe_queue.json"
    monkeypatch.setattr(C, "QUEUE", q)
    return q
