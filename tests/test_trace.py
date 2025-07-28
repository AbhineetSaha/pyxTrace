import json
from pathlib import Path
import pyxtrace.core as core


def test_trace_session(tmp_path: Path) -> None:
    script = Path('examples/fibonacci.py')
    log_path = tmp_path / 'trace.jsonl'
    core.run_tracer(script, mode='demo', log_path=log_path)
    assert log_path.exists()
    data = log_path.read_text().splitlines()
    assert data, 'log should not be empty'
    event = json.loads(data[0])
    assert 'kind' in event
