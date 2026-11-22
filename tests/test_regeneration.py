"""Asking for the same thing again, differently.

A prompt was deterministic output from the spec: the only way to change it
was to edit the YAML. Now a note rides with it, is kept beside the version,
and the take that comes back says where it came from.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from shotforge import actions
from shotforge.pipeline import approve
from shotforge.skills import prompt_history, read_prompt_note
from shotforge.skills.render import prompt_note_path
from shotforge.takes import TakeKind, add_take
from shotforge.web.app import create_app

NOTE = "warmer, less rain on the glass"


@pytest.fixture
def client(project):
    return TestClient(create_app(project.paths.root))


def _to_frame_stage(project, state):
    for _ in range(2):
        approve(project, state, "s01_pub", "sh01")
        actions.begin(project, state, "s01_pub", "sh01")


# -- the note reaches the prompt -------------------------------------------


def test_the_note_goes_into_the_prompt(project, state):
    _to_frame_stage(project, state)
    result = actions.build_prompt(project, state, "s01_pub", "sh01", note=NOTE)
    assert "THIS TIME" in result.text
    assert NOTE in result.text


def test_a_prompt_without_a_note_says_nothing_extra(project, state):
    _to_frame_stage(project, state)
    result = actions.build_prompt(project, state, "s01_pub", "sh01")
    assert "THIS TIME" not in result.text


def test_the_note_is_kept_beside_the_prompt_not_inside_it(project, state):
    """The .txt is pasted into ChatGPT whole; nothing may ride along in it."""
    _to_frame_stage(project, state)
    result = actions.build_prompt(project, state, "s01_pub", "sh01", note=NOTE)
    sidecar = prompt_note_path(result.path)
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["note"] == NOTE
    assert "written" not in result.path.read_text(encoding="utf-8")


def test_each_regeneration_is_its_own_version(project, state):
    _to_frame_stage(project, state)
    first = actions.build_prompt(project, state, "s01_pub", "sh01", note="one")
    second = actions.build_prompt(project, state, "s01_pub", "sh01", note="two")
    assert first.path != second.path
    assert read_prompt_note(first.path)["note"] == "one"
    assert read_prompt_note(second.path)["note"] == "two"


def test_the_history_reads_newest_first(project, state):
    _to_frame_stage(project, state)
    actions.build_prompt(project, state, "s01_pub", "sh01")
    actions.build_prompt(project, state, "s01_pub", "sh01", note=NOTE)
    history = prompt_history(project, "s01_pub", "sh01", "chatgpt-first-frame")
    assert [entry["version"] for entry in history] == [2, 1]
    assert history[0]["note"] == NOTE
    assert history[1]["note"] == ""


# -- the take remembers what produced it ------------------------------------


def test_a_filed_take_records_the_prompt_it_answered(project, state, png):
    _to_frame_stage(project, state)
    actions.build_prompt(project, state, "s01_pub", "sh01", note=NOTE)
    take = add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    origin = actions.note_take_origin(project, state, "s01_pub", "sh01", take)
    assert origin["prompt"] == "chatgpt-first-frame_v1"
    assert origin["note"] == NOTE
    assert state.take_origins("s01_pub", "sh01", "frame")[1] == origin


def test_a_later_regeneration_does_not_rewrite_an_earlier_take(project, state, png):
    """What a take came from is history; the next prompt must not edit it."""
    _to_frame_stage(project, state)
    actions.build_prompt(project, state, "s01_pub", "sh01", note="first try")
    first = add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    actions.note_take_origin(project, state, "s01_pub", "sh01", first)

    actions.build_prompt(project, state, "s01_pub", "sh01", note="second try")
    second = add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("b.png"))
    actions.note_take_origin(project, state, "s01_pub", "sh01", second)

    origins = state.take_origins("s01_pub", "sh01", "frame")
    assert origins[1]["note"] == "first try"
    assert origins[2]["note"] == "second try"


def test_origins_survive_a_reload(project, state, png):
    _to_frame_stage(project, state)
    actions.build_prompt(project, state, "s01_pub", "sh01", note=NOTE)
    take = add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    actions.note_take_origin(project, state, "s01_pub", "sh01", take)
    from shotforge.state import ProjectState

    assert ProjectState(project.paths).take_origins("s01_pub", "sh01", "frame")[1]["note"] == NOTE


# -- through the cockpit ----------------------------------------------------


def test_the_card_asks_what_to_change(client, project, state):
    _to_frame_stage(project, state)
    page = client.get("/shot/s01_pub/sh01").text
    assert 'name="note"' in page
    assert "What to change this time" in page


def test_regenerating_through_the_cockpit_carries_the_note(client, project, state):
    _to_frame_stage(project, state)
    client.post("/shot/s01_pub/sh01/prompt")
    response = client.post("/shot/s01_pub/sh01/prompt", data={"note": NOTE})
    assert response.status_code == 200
    assert NOTE in response.text
    assert "2 versions of this prompt" in response.text


def test_a_take_shows_where_it_came_from(client, project, state, png):
    _to_frame_stage(project, state)
    client.post("/shot/s01_pub/sh01/prompt", data={"note": NOTE})
    image = png("drop.png")
    client.post(
        "/shot/s01_pub/sh01/upload",
        files={"files": ("drop.png", image.read_bytes(), "image/png")},
    )
    takes = client.get("/shot/s01_pub/sh01/region/see").text
    assert "chatgpt-first-frame_v1" in takes
    assert NOTE in takes
