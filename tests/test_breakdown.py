"""Exercise chapter-to-shot proposals through both approval surfaces."""

from __future__ import annotations

import json
import re
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge.cli import app
from shotforge.engine import ClaudeCodeEngine
from shotforge.models import Beat, Shot, ShotStatus
from shotforge.project import Project
from shotforge.web.app import create_app

FIRST = "s01_tram_stop"
SECOND = "s02_archive"
CHAPTER = """# Chapter 1

Neta waits at the tram stop. The bell rings, and she pockets the silver key.

At the archive, Neta opens the door with the silver key. “Звонок был слышен
даже здесь”, she says to herself as the door swings inward.
"""


def _shot(shot_id="sh01"):
    return {
        "id": shot_id,
        "duration_s": 6,
        "in_frame": [{"char": "neta", "eyeline": "toward the door"}],
        "beats": [
            {"t": [0, 3], "action": "Neta turns the silver key."},
            {"t": [3, 6], "speaker": "neta", "line": "Звонок слышен и здесь."},
        ],
        "status": "done",  # The engine has no authority to advance the stage.
    }


@pytest.fixture
def chapter_project(tmp_path):
    project = Project.create(tmp_path / "film", name="Tram and archive")
    (project.paths.source / "ch01.md").write_text(CHAPTER, encoding="utf-8")
    return project


@pytest.fixture
def model(monkeypatch, chapter_project):
    responses = {
        "book": {
            "scenes": [
                {
                    "id": FIRST,
                    "location": "tram_stop",
                    "characters": ["neta"],
                    "props": ["silver_key"],
                    "from_book": ["Neta pockets the key at the tram stop."],
                },
                {
                    "id": SECOND,
                    "location": "archive",
                    "characters": ["neta"],
                    "props": ["silver_key"],
                    "from_book": ["Neta uses the silver key to open the archive."],
                    "design_choices": ["Let the door swing toward the camera."],
                },
            ],
            "characters": [{"id": "neta", "from_book": ["Neta carries the key."]}],
            "locations": [
                {"id": "tram_stop", "from_book": ["The tram has a stop with a bell."]},
                {"id": "archive", "from_book": ["The archive has a locked door."]},
            ],
            "props": [{
                "id": "silver_key",
                "from_book": ["The key is silver."],
                "design_choices": ["Choose an antique shape."],
            }],
        },
        FIRST: {"shots": [{
            "id": "sh01",
            "duration_s": 3,
            "in_frame": [{"char": "neta"}],
            "beats": [{"t": [0, 3], "action": "Neta pockets the key."}],
        }]},
        SECOND: {"shots": [_shot()]},
    }
    monkeypatch.setattr(ClaudeCodeEngine, "executable", lambda self: Path("claude"))

    def run(command, **kwargs):
        assert command[:2] == ["claude", "-p"]
        assert command[command.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in command
        assert kwargs["cwd"] == str(chapter_project.paths.root)
        prompt = kwargs["input"]
        assert "The bell rings" in prompt and "Звонок" in prompt
        if "skill `book-breakdown`" in prompt:
            value = json.dumps(responses["book"], ensure_ascii=False)
        else:
            assert "skill `scene-to-shots`" in prompt
            scene_id = re.search(r"\nscene:\n  id: (.+)\n", prompt)[1]
            value = f"```json\n{json.dumps(responses[scene_id], ensure_ascii=False)}\n```"
        return CompletedProcess(command, 0, json.dumps({"result": value}), "")

    monkeypatch.setattr("shotforge.engine.claude_code.subprocess.run", run)
    return responses


def test_cli_accepts_scene_two_from_prose_and_preserves_existing_shots(chapter_project, model):
    project = chapter_project
    runner = CliRunner()
    options = ["-C", str(project.paths.root)]

    preview = runner.invoke(app, ["breakdown", "ch01", *options], input="n\n")
    assert preview.exit_code == 0, preview.output
    assert f"+++ 04_scenes/{SECOND}/scene.yaml" in preview.output
    assert "+++ 01_bible/props/silver_key.yaml" in preview.output
    assert not project.scene_ids() and not project.props()

    saved = runner.invoke(app, ["breakdown", "ch01", *options], input="y\n")
    assert saved.exit_code == 0, saved.output
    assert project.scene(SECOND).chapter == "ch01.md"
    assert project.scene(SECOND).from_book == ["Neta uses the silver key to open the archive."]
    assert project.props()["silver_key"].design_choices == ["Choose an antique shape."]

    preview = runner.invoke(app, ["shots", SECOND, *options], input="n\n")
    assert preview.exit_code == 0, preview.output
    shot_path = project.paths.shot(SECOND, "sh01").spec
    assert f"+++ {project.paths.rel(shot_path)}" in preview.output
    assert "Звонок слышен и здесь." in preview.output
    assert not shot_path.exists()

    saved = runner.invoke(app, ["shots", SECOND, *options], input="y\n")
    assert saved.exit_code == 0, saved.output
    assert project.shot(SECOND, "sh01").status is ShotStatus.breakdown
    assert project.validate() == []
    approved = runner.invoke(app, ["approve", SECOND, "sh01", *options])
    assert approved.exit_code == 0, approved.output
    original = shot_path.read_bytes()

    model[SECOND]["shots"] = [_shot(), _shot("sh02")]
    saved = runner.invoke(app, ["shots", SECOND, *options], input="y\n")
    assert saved.exit_code == 0, saved.output
    assert f"Skipped (id collision): {project.paths.rel(shot_path)}" in saved.output
    assert "+++ " + project.paths.rel(shot_path) not in saved.output
    assert shot_path.read_bytes() == original
    assert project.shot(SECOND, "sh01").status is ShotStatus.breakdown_approved
    assert project.shot(SECOND, "sh02").status is ShotStatus.breakdown


def test_cockpit_approves_edited_beats_together_and_reports_late_collision(chapter_project, model):
    project = chapter_project
    client = TestClient(create_app(project.paths.root))
    assert "Chapter text" in client.get("/breakdown").text
    preview = client.post("/breakdown/propose", data={"chapter": "ch01.md"})
    assert preview.status_code == 200, preview.text
    assert "Звонок был слышен" in preview.text
    assert "Let the door swing toward the camera." in preview.text
    assert "Approve and save" in preview.text
    assert not project.scene_ids()
    assert not project.paths.approvals_log.exists()

    token = re.search('name="token" value="([^"]+)"', preview.text)[1]
    key = f"{SECOND}.sh01.0."
    form = {"token": token, key + "start": "0", key + "end": "4", key + "action": "Neta listens."}
    invalid = client.post("/breakdown/approve", data=form)
    assert invalid.status_code == 400
    assert "overlaps" in invalid.text
    assert "Neta listens." in invalid.text
    assert not project.scene_ids()
    assert not project.props()

    existing = Shot(
        id="sh01", duration_s=1, beats=[Beat(t=(0, 1), action="Already approved")],
        status=ShotStatus.breakdown_approved,
    )
    path = project.save_shot(FIRST, existing)
    original = path.read_bytes()
    form[key + "end"] = "3"
    saved = client.post("/breakdown/approve", data=form)
    assert saved.status_code == 200, saved.text
    assert f'href="/scene/{SECOND}"' in saved.text
    assert "Skipped ID collisions" in saved.text
    assert project.paths.rel(path) in saved.text
    assert path.read_bytes() == original
    assert project.shot(SECOND, "sh01").beats[0].action == "Neta listens."
    assert project.shot(SECOND, "sh01").status is ShotStatus.breakdown
    assert not project.paths.approvals_log.exists()
    assert client.post("/breakdown/approve", data=form).status_code == 409


def test_invalid_later_shot_disallows_the_entire_cockpit_proposal(chapter_project, model):
    model[SECOND]["shots"][0]["duration_s"] = 1
    client = TestClient(create_app(chapter_project.paths.root))
    response = client.post("/breakdown/propose", data={"chapter": "ch01.md"})
    assert response.status_code == 400
    assert "beats run to" in response.text
    assert not chapter_project.scene_ids()
    assert not chapter_project.characters()
