"""The cockpit screens, driven through the app rather than a browser."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shotforge import actions
from shotforge.pipeline import approve
from shotforge.takes import TakeKind, add_take
from shotforge.web.app import create_app


@pytest.fixture
def client(project):
    return TestClient(create_app(project.paths.root))


def _to_frame_stage(project, state):
    for _ in range(2):
        approve(project, state, "s01_pub", "sh01")
        actions.begin(project, state, "s01_pub", "sh01")


def test_the_index_lists_the_scene_and_its_shots(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "s01_pub" in response.text
    assert "sh01" in response.text


def test_the_storyboard_shows_one_card_per_shot(client):
    response = client.get("/scene/s01_pub")
    assert response.status_code == 200
    assert response.text.count('href="/shot/s01_pub/') >= 2


def test_a_missing_scene_is_a_404(client):
    assert client.get("/scene/nope").status_code == 404


def test_the_shot_card_says_what_to_do_now(client):
    response = client.get("/shot/s01_pub/sh01")
    assert response.status_code == 200
    assert "Review the beats and the camera" in response.text
    assert "Approve the shot list and beats" in response.text


def test_approving_through_the_card_moves_the_shot(client, project):
    response = client.post("/shot/s01_pub/sh01/approve", data={"note": "fine"})
    assert response.status_code == 200
    assert project.shot("s01_pub", "sh01").status.value == "breakdown_approved"
    assert "Start Blockout" in response.text


def test_the_prompt_button_renders_and_saves_a_prompt(client, project, state):
    _to_frame_stage(project, state)
    response = client.post("/shot/s01_pub/sh01/prompt")
    assert response.status_code == 200
    assert "Aspect ratio: 16:9" in response.text
    saved = list(project.paths.shot("s01_pub", "sh01").prompts.glob("*.txt"))
    assert [p.name for p in saved] == ["chatgpt-first-frame_v1.txt"]


def test_approving_without_a_take_is_a_clear_400(client, project, state):
    _to_frame_stage(project, state)
    response = client.post("/shot/s01_pub/sh01/approve")
    assert response.status_code == 400
    assert "no frame take" in response.text


def test_uploading_a_frame_files_it_as_a_take(client, project, state, png):
    _to_frame_stage(project, state)
    image = png("drop.png")
    response = client.post(
        "/shot/s01_pub/sh01/upload",
        files={"files": ("drop.png", image.read_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert (project.paths.shot("s01_pub", "sh01").frames / "first_v1.png").is_file()
    assert "v1" in response.text


def test_uploading_something_else_is_refused(client, project, state):
    _to_frame_stage(project, state)
    response = client.post(
        "/shot/s01_pub/sh01/upload",
        files={"files": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert not list(project.paths.inbox.glob("notes.txt"))


def test_uploading_a_video_runs_qa_and_shows_its_report(client, project, mp4):
    clip = mp4(seconds=10)
    response = client.post(
        "/shot/s01_pub/sh01/upload",
        files={"files": ("drop.mp4", clip.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 200
    assert (project.paths.shot("s01_pub", "sh01").qa / "video_v1.json").is_file()
    assert "10.0s against 10.0s in the spec" in response.text


def test_filing_from_the_inbox_needs_an_active_shot(client, project, png):
    (project.paths.inbox / "a.png").write_bytes(png("a.png").read_bytes())
    response = client.post("/inbox/file", data={"name": "a.png"})
    assert response.status_code == 400
    client.post("/shot/s01_pub/sh01/active")
    response = client.post("/inbox/file", data={"name": "a.png"})
    assert response.status_code == 200
    assert (project.paths.shot("s01_pub", "sh01").frames / "first_v1.png").is_file()


def test_media_cannot_be_read_from_outside_the_project(client):
    assert client.get("/media/../../etc/passwd").status_code in (403, 404)


def test_media_serves_a_project_file(client, project):
    url = "/media/" + project.paths.rel(project.paths.blockout("s01_pub", "sh01"))
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")


def test_qa_runs_on_the_latest_take_and_writes_a_report(client, project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("w.png", 1024, 768))
    response = client.post("/shot/s01_pub/sh01/qa", data={"kind": "frame"})
    assert response.status_code == 200
    assert "aspect ratio" in response.text
    assert (project.paths.shot("s01_pub", "sh01").qa / "frame_v1.json").is_file()


def test_the_bible_skills_and_log_screens_render(client):
    for path in ("/bible", "/skills", "/log"):
        assert client.get(path).status_code == 200
