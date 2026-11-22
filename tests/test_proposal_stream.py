"""Exercise the Claude Code stream with a real child process on each platform."""

import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest

from shotforge.engine import ClaudeCodeEngine, EngineError, EngineProgress
from shotforge.skills import find_skill


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "mock_claude.py"
    popen = subprocess.Popen
    processes = []

    def start(command, **kwargs):
        assert command[:2] == ["claude", "-p"]
        assert command[command.index("--output-format") + 1] == "stream-json"
        assert "--include-partial-messages" in command
        assert command[command.index("--tools") + 1] == ""
        process = popen([sys.executable, str(script)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("shotforge.engine.claude_code.subprocess.Popen", start)
    return script, processes


def test_streams_before_the_result_and_parses_final_fenced_json(fake_claude, tmp_path):
    script, _ = fake_claude
    marker = tmp_path / "progress"
    script.write_text(
        r"""import json, sys, time
from pathlib import Path

assert 'chapter_text' in sys.stdin.read()
print('{"type":"system","subtype":"init"}', flush=True)
print(json.dumps({"type": "stream_event", "event": {
    "type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"},
}}), flush=True)
marker = Path("progress")
deadline = time.monotonic() + 5
while not marker.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert marker.exists(), "Progress callback did not run before completion"
print(json.dumps({"type": "result", "result": '\x60\x60\x60json\n{"scenes": []}\n\x60\x60\x60'}))
""",
        encoding="utf-8",
    )
    engine = ClaudeCodeEngine(executable=Path("claude"), timeout_s=10)
    result = engine.run_skill_with_progress(
        find_skill("book-breakdown"), {"chapter_text": "Ferry crossing"},
        cwd=tmp_path, progress=EngineProgress(marker.touch, Event()),
    )
    assert result.structured() == {"scenes": []}


@pytest.mark.parametrize("cancel", [True, False])
def test_stops_a_child_that_has_not_returned_a_result(fake_claude, tmp_path, cancel):
    script, processes = fake_claude
    script.write_text(
        """import time
print('{"type":"assistant","message":{"content":[{"type":"text","text":"Draft"}]}}', flush=True)
time.sleep(30)
""",
        encoding="utf-8",
    )
    cancelled = Event()
    started = cancelled.set if cancel else lambda: None
    engine = ClaudeCodeEngine(executable=Path("claude"), timeout_s=5 if cancel else 1)
    expected = "generation was cancelled" if cancel else "did not finish within 1s"
    with pytest.raises(EngineError, match=expected):
        engine.run_skill_with_progress(
            find_skill("book-breakdown"), {}, cwd=tmp_path,
            progress=EngineProgress(started, cancelled),
        )
    assert len(processes) == 1 and processes[0].poll() is not None
