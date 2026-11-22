"""The shot card's regions redraw on their own.

Before this, sixteen interactions all targeted `#shot-card`, so changing a
prompt rebuilt the takes, the checks and the gate with it. Each region now
owns its own target, and tells the others only when they are actually
affected.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shotforge import actions
from shotforge.pipeline import approve
from shotforge.takes import TakeKind, add_take
from shotforge.web.app import REGIONS, create_app


@pytest.fixture
def client(project):
    return TestClient(create_app(project.paths.root))


def _to_frame_stage(project, state):
    for _ in range(2):
        approve(project, state, "s01_pub", "sh01")
        actions.begin(project, state, "s01_pub", "sh01")


def _triggers(response) -> set[str]:
    header = response.headers.get("HX-Trigger") or response.headers.get("HX-Trigger-After-Swap")
    return {part.strip() for part in header.split(",")} if header else set()


# -- the regions themselves -------------------------------------------------


@pytest.mark.parametrize("name", sorted(REGIONS))
def test_every_region_renders_on_its_own(client, name):
    response = client.get(f"/shot/s01_pub/sh01/region/{name}")
    assert response.status_code == 200
    assert response.text.strip()


def test_an_unknown_region_is_a_404(client):
    assert client.get("/shot/s01_pub/sh01/region/nope").status_code == 404


def test_the_page_holds_each_region_once(client):
    page = client.get("/shot/s01_pub/sh01").text
    for marker in ('id="shot-head"', 'id="do"', 'id="see"', 'id="judge"', 'id="gatebar"'):
        assert page.count(marker) == 1, marker


# -- what each action redraws ----------------------------------------------


def test_a_prompt_redraws_the_work_region_only(client, project, state):
    _to_frame_stage(project, state)
    response = client.post("/shot/s01_pub/sh01/prompt")
    assert response.status_code == 200
    assert 'id="do"' in response.text
    assert 'id="shot-card"' not in response.text
    assert 'id="see"' not in response.text
    assert _triggers(response) == set()


def test_setting_a_take_aside_redraws_the_takes_and_tells_the_rest(client, project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("one.png"))
    response = client.post("/shot/s01_pub/sh01/take/frame/1/reject")
    assert response.status_code == 200
    assert 'id="see"' in response.text
    assert 'id="shot-card"' not in response.text
    assert _triggers(response) == {"judge-changed", "gate-changed"}


def test_uploading_a_take_redraws_the_takes_and_tells_the_rest(client, project, state, png):
    _to_frame_stage(project, state)
    image = png("drop.png")
    response = client.post(
        "/shot/s01_pub/sh01/upload",
        files={"files": ("drop.png", image.read_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert 'id="see"' in response.text
    assert _triggers(response) == {"judge-changed", "gate-changed"}


def test_approving_still_redraws_the_whole_card(client, project, state, png):
    """A stage change moves everything, so here the whole card is right."""
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("one.png"))
    response = client.post("/shot/s01_pub/sh01/approve")
    assert response.status_code == 200
    assert 'id="shot-card"' in response.text


# -- the gate ---------------------------------------------------------------


def test_the_gate_bar_carries_the_gate_and_why_it_is_blocked(client, project, state):
    _to_frame_stage(project, state)
    bar = client.get("/shot/s01_pub/sh01/region/gate").text
    assert "Approve the first frame" in bar
    assert "no frame take yet" in bar
    assert "disabled" in bar


def test_the_gate_bar_offers_the_next_stage_once_one_is_approved(client, project, state):
    approve(project, state, "s01_pub", "sh01")
    bar = client.get("/shot/s01_pub/sh01/region/gate").text
    assert "Start Blockout" in bar


def test_a_finished_shot_has_no_gate(client, project, state, png, mp4):
    _to_frame_stage(project, state)
    shot_paths = project.paths.shot("s01_pub", "sh01")
    add_take(shot_paths, TakeKind.frame, png("f.png"))
    actions.approve_frame(project, state, "s01_pub", "sh01", 1)
    actions.begin(project, state, "s01_pub", "sh01")
    add_take(shot_paths, TakeKind.video, mp4("v.mp4", 10))
    actions.approve_video(project, state, "s01_pub", "sh01", 1)
    actions.begin(project, state, "s01_pub", "sh01")
    bar = client.get("/shot/s01_pub/sh01/region/gate").text
    assert "<form" not in bar


# -- the stage path ---------------------------------------------------------


def test_the_stage_path_marks_where_the_shot_is(client, project, state):
    head = client.get("/shot/s01_pub/sh01/region/head").text
    assert 'aria-current="step"' in head
    assert "Breakdown" in head and "Video" in head


def test_earlier_stages_are_offered_back_once_one_is_approved(client, project, state):
    assert "Undo approval" not in client.get("/shot/s01_pub/sh01/region/head").text
    approve(project, state, "s01_pub", "sh01")
    actions.begin(project, state, "s01_pub", "sh01")
    head = client.get("/shot/s01_pub/sh01/region/head").text
    assert "Undo approval" in head
    assert 'name="stage" value="breakdown"' in head
