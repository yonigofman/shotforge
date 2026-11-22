"""Camera proposals use the same decisions and checks in the CLI and cockpit."""

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge import checks, cinematography
from shotforge.cli import app
from shotforge.engine import ClaudeCodeEngine
from shotforge.models import ShotStatus
from shotforge.models.blockout import BlockoutAxis
from shotforge.web.app import create_app

SCENE, SHOT = "s01_pub", "sh07"
URL = f"/shot/{SCENE}/{SHOT}"
PLAN = {
    "lens_mm": 50,
    "lens_why": "A 50 mm lens keeps Durik's raised finger and expression in the same frame.",
    "height": "eye level",
    "height_why": "Eye-level height keeps Dima and Durik on the same visual plane.",
    "angle": "eye level",
    "angle_why": "A level angle preserves the eyeline into Dima's reaction in sh08.",
    "techniques": [
        {
            "name": "over_the_shoulder",
            "why": "Dima's shoulder anchors the over-the-shoulder as Durik raises his finger.",
        },
    ],
}


@pytest.fixture
def engine(monkeypatch):
    plans = [PLAN]
    prompts = []

    def run(argv, **kwargs):
        tools_index = argv.index("--tools")
        assert argv[tools_index + 1] == ""
        prompts.append(kwargs["input"])
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"result": json.dumps(plans[0])}), ""
        )

    monkeypatch.setattr(ClaudeCodeEngine, "executable", lambda self: Path("claude"))
    monkeypatch.setattr("shotforge.engine.claude_code.subprocess.run", run)
    return plans, prompts


class _ProposalForm(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name") == "token":
            self.token = fields["value"]


def test_cli_reviews_the_sh07_diff_and_saves_only_on_confirmation(example_project, engine):
    _, prompts = engine
    path = example_project.paths.shot(SCENE, SHOT).spec
    before = path.read_bytes()
    prior = example_project.shot(SCENE, SHOT)
    args = ["camera", SCENE, SHOT, "-C", str(example_project.paths.root)]
    runner = CliRunner()

    declined = runner.invoke(app, args, input="\n")
    assert declined.exit_code == 0, declined.output
    assert "lens_mm: 50" in declined.output
    assert "+  height: eye level" in declined.output
    assert "over_the_shoulder" in declined.output
    assert "Apply this camera proposal?" in declined.output
    assert path.read_bytes() == before
    assert "hero_table.left" in prompts[0] and "axis_180:" in prompts[0]
    assert "previous_shot:\n  id: sh06" in prompts[0]
    assert "next_shot:\n  id: sh08" in prompts[0]
    assert "`bookend`" in prompts[0] and "`insert`" in prompts[0]

    confirmed = runner.invoke(app, args, input="y\n")
    assert confirmed.exit_code == 0, confirmed.output
    assert "Saved camera proposal" in confirmed.output
    saved = example_project.shot(SCENE, SHOT)
    assert saved.camera.lens_mm == 50
    assert saved.camera.height == saved.camera.angle == "eye level"
    assert saved.technique == ["over_the_shoulder"]
    assert saved.camera.why == " ".join(
        PLAN[key] for key in ("lens_why", "height_why", "angle_why")
    )
    assert saved.technique_why == PLAN["techniques"][0]["why"]
    assert saved.status is ShotStatus.breakdown
    assert saved.camera.position == prior.camera.position
    assert saved.in_frame == prior.in_frame and saved.beats == prior.beats
    assert saved.continuity == prior.continuity and saved.refs == prior.refs


def test_web_previews_and_applies_the_same_proposal(example_project, engine):
    _, prompts = engine
    client = TestClient(create_app(example_project.paths.root))
    path = example_project.paths.shot(SCENE, SHOT).spec
    before = path.read_bytes()
    assert "Propose camera" in client.get(URL).text

    preview = client.post(f"{URL}/camera")
    assert preview.status_code == 200
    assert "Proposed camera changes" in preview.text and "Apply camera changes" in preview.text
    assert path.read_bytes() == before
    form = _ProposalForm()
    form.feed(preview.text)
    assert form.token is not None

    data = {"token": form.token}
    assert client.post(f"/shot/{SCENE}/sh08/camera/apply", data=data).status_code == 409
    response = client.post(f"{URL}/camera/apply", data=data)
    assert response.status_code == 200
    assert "Lens 50 mm" in response.text and "Height eye level" in response.text
    assert "Durik&#39;s raised finger" in response.text
    assert "over the shoulder" in response.text
    assert len(prompts) == 1
    assert client.post(f"{URL}/camera/apply", data=data).status_code == 409


@pytest.mark.parametrize("position", [(0, -2), (0, 0)])
def test_cli_refuses_an_axis_crossing_before_confirmation(example_project, engine, position):
    scene = example_project.scene(SCENE)
    scene.axis_180 = BlockoutAxis(start=(-2, 0), end=(2, 0), room_side=(0, 1))
    example_project.save_scene(scene)
    shot = example_project.shot(SCENE, SHOT)
    shot.camera.position = position
    example_project.save_shot(SCENE, shot)
    path = example_project.paths.shot(SCENE, SHOT).spec
    before = path.read_bytes()

    result = CliRunner().invoke(app, ["camera", SCENE, SHOT, "-C", str(example_project.paths.root)])
    assert result.exit_code == 1, result.output
    assert "must stay on the room side of the 180° line" in result.output
    assert "Apply this camera proposal?" not in result.output
    assert path.read_bytes() == before
    reverse = example_project.shot(SCENE, "sh08")
    assert any(
        f.check == "axis_180" and f.failed and SHOT in f.message
        for f in checks.check_spec(example_project, SCENE, reverse)
    )


def test_ots_needs_a_foreground_and_proposal_labels_come_from_the_library(example_project, engine):
    plans, _ = engine
    shot = example_project.shot(SCENE, SHOT)
    shot.in_frame[0].role = "background"
    example_project.save_shot(SCENE, shot)
    assert any(
        f.check == "technique" and f.failed for f in checks.check_spec(example_project, SCENE, shot)
    )
    with pytest.raises(cinematography.CameraError, match="needs another character"):
        cinematography.propose_camera(example_project, SCENE, SHOT)

    plans[0] = {**PLAN, "techniques": [{"name": "anything", "why": "It fits."}]}
    with pytest.raises(cinematography.CameraError, match="invalid camera plan"):
        cinematography.propose_camera(example_project, SCENE, SHOT)


def test_a_pending_proposal_cannot_overwrite_new_work_or_edit_an_approved_shot(
    example_project, engine
):
    proposal, _ = cinematography.propose_camera(example_project, SCENE, SHOT)
    neighbour = example_project.shot(SCENE, "sh08")
    neighbour.summary = "New reaction beat"
    example_project.save_shot(SCENE, neighbour)
    with pytest.raises(cinematography.CameraError, match="specs changed"):
        cinematography.apply_camera(example_project, SCENE, SHOT, proposal)
    shot = example_project.shot(SCENE, SHOT)
    assert shot.camera.why is None and shot.technique_why is None
    shot.status = ShotStatus.breakdown_approved
    example_project.save_shot(SCENE, shot)
    with pytest.raises(cinematography.CameraError, match="in Breakdown"):
        cinematography.apply_camera(example_project, SCENE, SHOT, proposal)
    client = TestClient(create_app(example_project.paths.root))
    assert "Propose camera" not in client.get(URL).text
