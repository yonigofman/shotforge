from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge import actions, frame_qa
from shotforge.cli import app
from shotforge.engine import ClaudeCodeEngine, EngineCancelled, EngineProgress, EngineResult
from shotforge.engine import claude_code as claude_mod
from shotforge.takes import TakeKind, add_take
from shotforge.web.app import create_app
from shotforge.yamlio import dump

SCENE, SHOT = "s01_pub", "sh02"


def test_vision_uses_the_requested_take_references_and_claude_read(project, png, monkeypatch):
    paths = project.paths
    shot = paths.shot(SCENE, SHOT)
    first = add_take(shot, TakeKind.frame, png("first.png"))
    add_take(shot, TakeKind.frame, png("second.png"))
    crop = paths.face_crops("durik") / "seed.png"
    blockout = paths.blockout(SCENE, SHOT)
    wall = paths.design_dir("location", "pub") / "wall_ref.png"
    last_frame = paths.shot(SCENE, "sh01").last_frame
    last_frame.write_bytes(first.path.read_bytes())
    rows = [{"check": name, "level": "pass", "message": "Looks correct"} for name in frame_qa.ROWS]

    def run(command, **kwargs):
        prompt = command[2]
        assert "--tools" in command and command[command.index("--tools") + 1] == "Read"
        assert kwargs["cwd"] == str(paths.root)
        for path in (first.path, crop, blockout, wall, last_frame):
            assert str(path) in prompt
        assert "first_v2.png" not in prompt
        assert "previous_continuity_out:\n  beer: half" in prompt
        assert "eyeline: Dima's face" in prompt
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": json.dumps({"findings": rows}), "is_error": False}),
        )

    engine = ClaudeCodeEngine(executable=Path("claude"))
    monkeypatch.setattr(frame_qa, "get_engine", lambda name: engine)
    monkeypatch.setattr(claude_mod, "subprocess", SimpleNamespace(run=run))
    findings = actions.run_qa(project, SCENE, SHOT, TakeKind.frame, first.number)
    assert [finding.check for finding in findings] == ["aspect ratio", *frame_qa.ROWS]
    assert actions.read_qa_report(first.qa_report(shot)) == findings


def test_cli_vision_switch_overrides_project_default(project, png, monkeypatch):
    project.config.vision_qa = False
    dump(project.paths.config, project.config)
    shot = project.paths.shot(SCENE, SHOT)
    take = add_take(shot, TakeKind.frame, png())
    calls = []

    def review(*args, **kwargs):
        calls.append(args)
        return []

    monkeypatch.setattr(frame_qa, "review", review)
    args = ["qa", SCENE, SHOT, "-C", str(project.paths.root)]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 0
    assert not calls
    assert runner.invoke(app, [*args, "--vision"]).exit_code == 0
    assert len(calls) == 1
    assert runner.invoke(app, [*args, "--no-vision"]).exit_code == 0
    assert len(calls) == 1
    assert take.qa_report(shot).is_file()


def test_missing_claude_keeps_code_findings_and_writes_an_advisory_report(project, png):
    shot = project.paths.shot(SCENE, SHOT)
    take = add_take(shot, TakeKind.frame, png())
    findings = actions.run_qa(project, SCENE, SHOT, TakeKind.frame, take.number)
    assert [(finding.check, finding.level) for finding in findings] == [
        ("aspect ratio", "pass"), ("vision", "warn")
    ]
    assert "Claude Code not found" in findings[-1].message
    assert actions.read_qa_report(take.qa_report(shot)) == findings


def test_expired_claude_oauth_explains_how_to_sign_in_again(project, png, monkeypatch):
    shot = project.paths.shot(SCENE, SHOT)
    take = add_take(shot, TakeKind.frame, png())
    engine = ClaudeCodeEngine(executable=Path("claude"))
    monkeypatch.setattr(frame_qa, "get_engine", lambda name: engine)
    failure = SimpleNamespace(
        returncode=1,
        stderr="Failed to authenticate: OAuth session expired and could not be refreshed",
        stdout="",
    )
    monkeypatch.setattr(claude_mod.subprocess, "run", lambda *args, **kwargs: failure)

    findings = actions.run_qa(project, SCENE, SHOT, TakeKind.frame, take.number)
    assert [(finding.check, finding.level) for finding in findings] == [
        ("aspect ratio", "pass"), ("vision", "warn")
    ]
    assert "OAuth session expired" in findings[-1].message
    assert "claude auth login" in findings[-1].message
    assert actions.read_qa_report(take.qa_report(shot)) == findings


def test_frame_check_streams_review_and_cancel_preserves_report(project, png, monkeypatch):
    paths = project.paths.shot(SCENE, SHOT)
    take = add_take(paths, TakeKind.frame, png())
    payload = {"findings": [{"check": "identity", "level": "pass", "message": "Matches the take."}]}

    def review(self, skill, inputs, *, cwd, files, progress):
        assert take.path in files
        progress.on_output()
        return EngineResult(text=json.dumps(payload))

    monkeypatch.setattr(ClaudeCodeEngine, "run_skill_with_progress", review)
    client = TestClient(create_app(project.paths.root))
    response = client.post(f"/shot/{SCENE}/{SHOT}/qa", data={"kind": "frame"},
                           headers={"Accept": "application/x-ndjson"})
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert {"type": "status", "message": "Checking frame size…"} in events
    assert {"type": "status", "message": "Claude is reviewing the frame…"} in events
    assert events[-1]["trigger"] == "card-changed"
    assert "Matches the take." in events[-1]["html"]
    report = take.qa_report(paths)
    previous = report.read_bytes()
    payload["findings"][0]["message"] = "A cancelled result."
    cancelled = Event()
    with pytest.raises(EngineCancelled):
        actions.run_qa(
            project, SCENE, SHOT, TakeKind.frame, take.number,
            progress=EngineProgress(cancelled.set, cancelled),
        )
    assert report.read_bytes() == previous
