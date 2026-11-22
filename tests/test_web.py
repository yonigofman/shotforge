"""The cockpit screens, driven through the app rather than a browser."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from shotforge import actions, checks
from shotforge.models.shot import FramedCharacter
from shotforge.pipeline import approve
from shotforge.skills import find_skill
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


def test_identity_fix_and_explicit_rule_promotion(client, project, state, png, monkeypatch):
    _to_frame_stage(project, state)
    scene, shot_id = "s01_pub", "sh01"
    url = f"/shot/{scene}/{shot_id}"
    shot_paths = project.paths.shot(scene, shot_id)
    shot = project.shot(scene, shot_id)
    shot.in_frame.append(FramedCharacter(char="durik", role="other"))
    project.save_shot(scene, shot)
    skill = find_skill("chatgpt-identity-pass")
    shutil.copytree(skill.root, project.paths.skills / skill.name)
    skill = find_skill(skill.name, project.paths.skills)
    original_rules = skill.rules()
    correction = "Match Dima's age to the reference."
    failure = checks.Finding(
        "identity", checks.FAIL, "Dima looks too old next to Durik.", correction
    )
    monkeypatch.setattr(checks, "check_frame_file", lambda *_: [failure])
    frame = png()
    add_take(shot_paths, TakeKind.frame, frame)
    report = client.post(f"{url}/qa")
    assert f'hx-post="{url}/fix"' in report.text
    assert failure.key in client.get(url).text

    response = client.post(f"{url}/fix", data={"take": 1, "finding": failure.key})
    assert response.status_code == 200
    assert 'id="prompt-text"' in response.text and "Copy prompt" in response.text
    assert '<img src="/media/03_blender/renders/s01_pub/sh01/mask_dima.png' in response.text
    prompt = (shot_paths.prompts / "chatgpt-fix_v1.txt").read_text(encoding="utf-8")
    assert "Selection edit" in prompt and "Freeze the composition" in prompt
    assert "Match dima's face to seed.png" in prompt
    assert "mask_durik.png" not in prompt
    assert "first_v1.png" in prompt
    assert client.post(f"{url}/fix", data={"take": 1, "finding": failure.key}).status_code == 200
    assert (shot_paths.prompts / "chatgpt-fix_v2.txt").is_file()

    add_take(shot_paths, TakeKind.frame, frame)
    other = checks.Finding("identity", checks.PASS, "Durik matches.", target="durik")
    monkeypatch.setattr(checks, "check_frame_file", lambda *_: [other])
    client.post(f"{url}/qa", data={"take": 2})
    approved = client.post(f"{url}/approve", data={"take": 2})
    assert approved.status_code == 200
    assert "Add to skill rules" not in approved.text

    passed = checks.Finding("identity", checks.PASS, "All visible faces match their crops.")
    monkeypatch.setattr(checks, "check_frame_file", lambda *_: [passed])
    qa = client.post(f"{url}/qa", data={"take": 2})
    assert qa.text.count("Add to skill rules") == 1
    assert 'action="/skills/chatgpt-identity-pass/rules"' in qa.text
    assert "Add to skill rules" in client.get(url).text
    assert skill.rules() == original_rules
    rule = f"Match dima's face to the approved face crop and preserve the composition. {correction}"
    assert client.post(f"/skills/{skill.name}/rules", data={"rule": rule}).status_code == 200
    assert rule in skill.rules()
    assert "Add to skill rules" not in client.get(url).text


@pytest.mark.parametrize(
    ("check", "mode", "guide"),
    [
        ("prop continuity", "Selection edit", True),
        ("window", "Selection edit", True),
        ("AI look", "Full edit", False),
        ("grade", "Full edit", False),
    ],
)
def test_fix_uses_the_reported_take_and_edit_mode(
    client, project, png, monkeypatch, check, mode, guide
):
    scene, shot_id = "s01_pub", "sh01"
    url = f"/shot/{scene}/{shot_id}"
    shot_paths = project.paths.shot(scene, shot_id)
    frame = png()
    add_take(shot_paths, TakeKind.frame, frame)
    add_take(shot_paths, TakeKind.frame, frame)
    failure = checks.Finding(
        check, checks.FAIL, "The detail drifted.", "Match the shot spec.",
        region="the upper-left corner",
    )
    monkeypatch.setattr(checks, "check_frame_file", lambda *_: [failure])
    client.post(f"{url}/qa", data={"take": 1})
    response = client.post(f"{url}/fix", data={"take": 1, "finding": failure.key})
    assert response.status_code == 200
    prompt = (shot_paths.prompts / "chatgpt-fix_v1.txt").read_text(encoding="utf-8")
    assert mode in prompt
    assert "first_v1.png" in prompt and "first_v2.png" not in prompt
    assert ("the upper-left corner" in prompt) == guide
    assert ("blockout.png" in prompt) == guide
    assert client.post(f"{url}/fix", data={"take": 2, "finding": failure.key}).status_code == 400
