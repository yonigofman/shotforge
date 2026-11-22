from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge import continuity
from shotforge.cli import app
from shotforge.engine import ClaudeCodeEngine, EngineResult
from shotforge.web.app import create_app

SCENE = "s01_pub"
CUT = "sh01 → sh02"
GAP = "The mug changes hands across the cut."


@pytest.fixture
def approved_frames(project, png):
    earlier = project.paths.shot(SCENE, "sh01")
    later = project.paths.shot(SCENE, "sh02")
    frames = [earlier.approved_frame, earlier.last_frame, later.approved_frame]
    image = png(width=320, height=180).read_bytes()
    for path in frames:
        path.write_bytes(image)
    return frames


def test_storyboard_runs_the_skill_and_the_shot_card_and_cli_reuse_it(
    project, approved_frames, monkeypatch
):
    monkeypatch.setattr(ClaudeCodeEngine, "executable", lambda self: Path("claude"))
    verdict = {
        "findings": [{
            "check": "hands and gestures",
            "level": "fail",
            "message": GAP,
            "fix": "Keep the mug in the same hand.",
        }],
    }
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == str(project.paths.root)
        assert command[:2] == ["claude", "-p"]
        assert command[3:] == [
            "--output-format", "json", "--tools", "Read", "--allowedTools", "Read",
        ]
        prompt = command[2]
        for frame in approved_frames:
            assert str(frame) in prompt
        assert "earlier_last_frame:" in prompt
        assert "axis_180: room side" in prompt
        assert "continuity:" in prompt
        output = {"result": f"```json\n{json.dumps(verdict)}\n```", "is_error": False}
        return CompletedProcess(command, 0, json.dumps(output), "")

    monkeypatch.setattr("shotforge.engine.claude_code.subprocess.run", run)
    client = TestClient(create_app(project.paths.root))
    story = f"/scene/{SCENE}"
    card = f"/shot/{SCENE}/sh02"
    assert "Run continuity check" in client.get(story).text
    assert GAP not in client.get(card).text
    assert not calls

    response = client.post(f"{story}/continuity")
    assert response.status_code == 200
    assert f"{CUT}: {GAP}" in response.text
    assert "Keep the mug in the same hand." in response.text
    assert GAP in client.get(story).text
    assert GAP in client.get(card).text
    assert GAP not in client.get(f"/shot/{SCENE}/sh01").text

    args = ["continuity", SCENE, "-C", str(project.paths.root)]
    cli = CliRunner().invoke(app, args)
    assert cli.exit_code == 1
    assert f"{CUT}: {GAP}" in cli.output
    assert len(calls) == 1


def test_cache_tracks_inputs_and_claude_model(
    project, approved_frames, png, monkeypatch
):
    calls = []

    def run(self, skill, inputs, **kwargs):
        calls.append(inputs)
        return EngineResult(text='{"findings": []}')

    monkeypatch.setattr(ClaudeCodeEngine, "run_skill", run)
    scene = project.scene(SCENE)
    first = continuity.scene_findings(project, scene, run=True)
    assert first[0].level == "pass"
    assert continuity.scene_findings(project, scene, run=True) == first
    assert len(calls) == 1

    approved_frames[1].write_bytes(png("changed.png", width=640, height=360).read_bytes())
    assert continuity.scene_findings(project, scene)[0].level == "warn"
    assert continuity.scene_findings(project, scene, run=True) == first
    assert len(calls) == 2

    incoming = project.shot(SCENE, "sh02")
    incoming.beats[0].action = "Durik sets down the mug."
    project.save_shot(SCENE, incoming)
    assert continuity.scene_findings(project, scene, run=True) == first
    assert len(calls) == 3

    project.paths.claude_settings.write_text("model: sonnet\neffort: high\n", encoding="utf-8")
    assert continuity.scene_findings(project, scene)[0].level == "warn"
    assert continuity.scene_findings(project, scene, run=True) == first
    assert len(calls) == 4


def test_invalid_reviews_are_retried_and_missing_frames_skip_a_cut(
    project, approved_frames, monkeypatch
):
    third = project.shot(SCENE, "sh02").model_copy(update={"id": "sh03"})
    project.save_shot(SCENE, third)
    paths = project.paths.shot(SCENE, "sh03")
    paths.approved_frame.write_bytes(approved_frames[-1].read_bytes())
    outputs = iter([
        '{"findings": [{"check": "gaze", "level": "unknown", "message": "A gap."}]}',
        '{"findings": []}',
    ])

    def run(self, skill, inputs, **kwargs):
        assert inputs["later_shot"]["id"] == "sh02"
        return EngineResult(text=next(outputs))

    monkeypatch.setattr(ClaudeCodeEngine, "run_skill", run)
    scene = project.scene(SCENE)
    first, second = continuity.scene_findings(project, scene, run=True)
    assert first.failed
    assert "invalid findings" in first.message
    assert "sh02 → sh03: Skipped" in second.message
    assert "sh02/last_frame.png" in second.message
    assert not project.paths.cache.exists()

    first, second = continuity.scene_findings(project, scene, run=True)
    assert first.level == "pass"
    assert second.level == "warn"
