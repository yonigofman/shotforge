"""Blockout rendering from the CLI and cockpit, with and without Blender installed."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge import blender
from shotforge.cli import app
from shotforge.models import Location
from shotforge.models.blockout import BlockoutAxis
from shotforge.models.shot import ShotStatus
from shotforge.pipeline import next_action
from shotforge.state import ProjectState
from shotforge.tools import find_tool
from shotforge.web.app import create_app

SCENE = "s01_pub"
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def mcp_jobs(monkeypatch):
    jobs = []

    def render(_script, job, _timeout):
        payload = json.loads(job.read_text(encoding="utf-8"))
        jobs.append(payload)
        Path(payload["blend"]).write_bytes(b"blend")
        for path in payload["renders"].values():
            Path(path).write_bytes(PNG)
        for masks in payload["masks"].values():
            for path in map(Path, masks.values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(PNG)

    monkeypatch.setattr(blender.mcp, "render", render)
    return jobs


def test_cli_renders_the_scene_in_one_mcp_job(example_project, mcp_jobs):
    project = example_project
    result = CliRunner().invoke(app, ["blockout", SCENE, "-C", str(project.paths.root)])
    assert result.exit_code == 0, result.output
    assert len(mcp_jobs) == 1
    job = mcp_jobs[0]
    assert set(job["renders"]) == {"sh06", "sh07", "sh08"}
    assert {camera["name"] for camera in job["spec"]["cameras"].values()} == {
        "CAM_S06", "CAM_S07", "CAM_S08",
    }
    assert {figure["char"] for figure in job["spec"]["mannequins"]} == {
        "dima", "durik", "zhmurik",
    }
    assert project.paths.set_file("pub").is_file()
    assert all(project.paths.blockout(SCENE, shot).is_file() for shot in job["renders"])
    for shot in project.shots(SCENE):
        assert set(job["masks"][shot.id]) == set(shot.characters)
        assert all(project.paths.mask(SCENE, shot.id, char).is_file() for char in shot.characters)


def test_render_discovers_the_project(example_project, mcp_jobs, monkeypatch):
    monkeypatch.chdir(example_project.paths.root)
    assert blender.render(SCENE, "sh07") == example_project.paths.blockout(SCENE, "sh07")
    assert set(mcp_jobs[0]["renders"]) == {"sh07"}


def test_texture_changes_invalidate_only_scenes_that_use_them(example_project, mcp_jobs, png):
    project = example_project
    active = project.paths.textures / "pub_plaster.png"
    active.unlink()
    other = project.scene(SCENE).model_copy(update={"id": "s02_other", "location": "other"})
    project.save_entity("location", Location(id="other"))
    project.save_scene(other)
    project.save_shot(other.id, project.shot(SCENE, "sh07"))

    assert len(blender.render_scene(SCENE, project=project, changed=True)) == 3
    assert len(blender.render_scene(other.id, project=project, changed=True)) == 1
    assert all(not job["spec"]["set"]["materials"] for job in mcp_jobs)
    assert blender.render_scene(SCENE, project=project, changed=True) == []

    active.write_bytes(png(width=32, height=32).read_bytes())
    assert blender.render_scene(other.id, project=project, changed=True) == []
    blender.render(SCENE, "sh07", project=project)
    assert set(mcp_jobs[-1]["renders"]) == {"sh07"}
    assert mcp_jobs[-1]["spec"]["set"]["materials"]["wall"]["uv_scale"] == 0.85

    assert len(blender.render_scene(SCENE, project=project, changed=True)) == 2
    assert set(mcp_jobs[-1]["renders"]) == {"sh06", "sh08"}
    assert blender.render_scene(SCENE, project=project, changed=True) == []
    assert blender.render_scene(other.id, project=project, changed=True) == []

    project.paths.mask(SCENE, "sh07", "dima").unlink()
    assert blender.render_scene(SCENE, project=project, changed=True) == [
        project.paths.blockout(SCENE, "sh07"),
    ]


def test_shot_card_renders_one_png_and_makes_approval_ready(example_project, mcp_jobs):
    project = example_project
    client = TestClient(create_app(project.paths.root))
    url = f"/shot/{SCENE}/sh07"
    assert "Render blockout" not in client.get(url).text
    assert client.post(f"{url}/blockout").status_code == 400
    assert client.post(f"{url}/approve").status_code == 200
    response = client.post(f"{url}/begin")
    assert "Render blockout" in response.text
    assert "not rendered yet" in response.text

    response = client.post(f"{url}/blockout")
    assert response.status_code == 200
    assert "not rendered yet" not in response.text
    assert "Blockout render for sh07" in response.text
    assert set(mcp_jobs[0]["renders"]) == {"sh07"}
    assert set(mcp_jobs[0]["masks"]) == {"sh07"}
    assert len(mcp_jobs[0]["spec"]["cameras"]) == 3
    state = ProjectState(project.paths)
    assert next_action(project, state, SCENE, "sh07").ready
    assert project.shot(SCENE, "sh07").status is ShotStatus.blockout


def test_a_partial_mcp_result_falls_back_without_replacing_previous_renders(
    example_project, monkeypatch,
):
    project = example_project
    path = project.paths.blockout(SCENE, "sh07")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"previous blockout")
    attempts = []

    def partial(_script, job, *_args):
        attempts.append(job.parent)
        (job.parent / "pub.blend").write_bytes(b"blend")
        (job.parent / "sh07.png").write_bytes(PNG)
        assert path.read_bytes() == b"previous blockout"

    monkeypatch.setattr(blender.mcp, "render", partial)
    monkeypatch.setattr(blender.headless, "render", partial)
    with pytest.raises(blender.BlockoutError, match="did not produce mask_dima.png"):
        blender.render(SCENE, "sh07", project=project)
    assert len(set(attempts)) == 2
    assert path.read_bytes() == b"previous blockout"


def test_background_blender_renders_the_example_and_checks_every_camera(
    example_project, monkeypatch,
):
    if not find_tool("blender", overrides={}):
        pytest.skip("Blender is required for the background rendering integration test")

    def offline(*_args):
        raise blender.BlockoutError("MCP offline")

    monkeypatch.setattr(blender.mcp, "render", offline)
    project = example_project
    paths = blender.render_scene(SCENE, project=project)
    assert len(paths) == 3
    for path in paths:
        header = path.read_bytes()[:24]
        assert header[:8] == PNG
        assert struct.unpack(">II", header[16:24]) == (960, 540)
    for shot in project.shots(SCENE):
        for char in shot.characters:
            header = project.paths.mask(SCENE, shot.id, char).read_bytes()[:26]
            assert header[:8] == PNG
            assert struct.unpack(">IIBB", header[16:26]) == (960, 540, 8, 0)
    assert not project.paths.mask(SCENE, "sh08", "zhmurik").exists()

    blend = project.paths.set_file("pub")
    original = blend.read_bytes()
    scene = project.scene(SCENE)
    scene.axis_180 = BlockoutAxis(start=(-2, -0.6), end=(2, -0.8), room_side=(0, 1))
    project.save_scene(scene)
    reverse = project.shot(SCENE, "sh08")
    reverse.camera.position = (0.0, -1.9)
    project.save_shot(SCENE, reverse)
    with pytest.raises(blender.BlockoutError, match=r"sh08: camera CAM_S08.*scene.axis_180"):
        blender.render(SCENE, "sh07", project=project)
    assert blend.read_bytes() == original


def test_two_scenes_in_one_location_share_a_set(example_project, mcp_jobs):
    """The set is the place, so a second scene in the pub opens the first one's."""
    project = example_project
    elsewhere = project.scene(SCENE).model_copy(update={"id": "s09_pub_again"})
    project.save_scene(elsewhere)
    project.save_shot(elsewhere.id, project.shot(SCENE, "sh07"))

    blender.render_scene(SCENE, project=project)
    blender.render_scene(elsewhere.id, project=project)

    assert [job["spec"]["set"]["location"] for job in mcp_jobs] == ["pub", "pub"]
    assert mcp_jobs[0]["set_blend"] is None, "the first job builds the set"
    assert mcp_jobs[1]["set_blend"], "the second job is handed the set that exists"
    assert Path(mcp_jobs[1]["set_blend"]).name.endswith("pub.blend")
    assert project.paths.set_file("pub").is_file()
    assert not project.paths.set_file(elsewhere.id).exists()
    # Each scene stages its own people over the same room.
    assert mcp_jobs[0]["spec"]["set"]["geometry"] == mcp_jobs[1]["spec"]["set"]["geometry"]
    assert {job["spec"]["scene"] for job in mcp_jobs} == {SCENE, elsewhere.id}


def test_a_dressed_set_is_not_rebuilt_when_the_room_changes(example_project, mcp_jobs):
    """Dressing is the director's: a wall that moves under it needs a say-so."""
    project = example_project
    blender.render_scene(SCENE, project=project)
    state = json.loads(project.paths.set_state("pub").read_text(encoding="utf-8"))
    assert state["dressing"] is False

    # The script reports dressing when the saved set carries work of its own.
    project.paths.set_state("pub").write_text(
        json.dumps({**state, "dressing": True}), encoding="utf-8"
    )
    pub = project.entity("location", "pub")
    pub.blockout = pub.blockout.model_copy(update={"room": (9.0, 5.0, 3.2)})
    project.save_entity("location", pub)

    before = len(mcp_jobs)
    with pytest.raises(blender.BlockoutError, match="dressing of its own"):
        blender.render_scene(SCENE, project=project)
    assert len(mcp_jobs) == before, "nothing was rendered over the dressed set"

    # Asked for plainly, it rebuilds and keeps what the director put there.
    assert blender.render_scene(SCENE, project=project, rebuild=True)
    assert mcp_jobs[-1]["spec"]["set"]["geometry"]["room"] == [9.0, 5.0, 3.2]
    assert mcp_jobs[-1]["set_blend"], "the rebuild still opens the dressed set"


def test_the_cockpit_shows_each_location_set_and_builds_it(example_project, mcp_jobs):
    project = example_project
    client = TestClient(create_app(project.paths.root))

    page = client.get("/sets").text
    assert "pub" in page and "Not built" in page
    assert "Build the set" in page

    assert client.post("/set/pub/build", follow_redirects=False).status_code == 303
    assert project.paths.set_file("pub").is_file()
    assert len(mcp_jobs) == 1
    assert "Built" in client.get("/sets").text

    # A location nothing is shot in cannot be built into anything.
    project.save_entity("location", Location(id="street"))
    page = client.get("/sets").text
    assert "street" in page and "no scene yet" in page
    assert client.post("/set/street/build").status_code == 400
