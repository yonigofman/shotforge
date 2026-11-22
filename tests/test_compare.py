"""Holding a take against something.

Judging a frame is never done on its own: it is held against the frame it
has to cut from, the sheet of the person in it, or the blockout it was
composed on. Before this there was no view for that.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shotforge import actions, compare
from shotforge.pipeline import approve
from shotforge.takes import TakeKind, add_take
from shotforge.web.app import create_app


@pytest.fixture
def client(project):
    return TestClient(create_app(project.paths.root))


def _to_frame_stage(project, state, shot_id="sh01"):
    for _ in range(2):
        approve(project, state, "s01_pub", shot_id)
        actions.begin(project, state, "s01_pub", shot_id)


# -- what there is to compare against ---------------------------------------


def test_the_blockout_is_always_there_to_compare_against(project):
    found = {r.key: r for r in compare.references(project, "s01_pub", "sh01")}
    assert found["blockout"].group == "This shot"


def test_takes_and_the_approved_frame_join_the_list(project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    actions.approve_frame(project, state, "s01_pub", "sh01", 1)
    keys = [r.key for r in compare.references(project, "s01_pub", "sh01")]
    assert "take:1" in keys
    assert "approved" in keys


def test_the_shot_before_brings_the_cut_in(project, state, png):
    _to_frame_stage(project, state, "sh01")
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    actions.approve_frame(project, state, "s01_pub", "sh01", 1)
    found = compare.references(project, "s01_pub", "sh02")
    assert any(r.key == "previous_frame" and r.group == "The cut in" for r in found)


def test_a_reference_that_is_not_on_disk_is_left_out(project):
    """Nothing in the picker that cannot be shown."""
    assert all(reference.exists for reference in compare.references(project, "s01_pub", "sh01"))
    assert not any(r.key == "approved" for r in compare.references(project, "s01_pub", "sh01"))


def test_sheets_and_crops_come_under_identity(project):
    found = {r.key: r for r in compare.references(project, "s01_pub", "sh01")}
    assert found["sheet:dima"].group == "Identity"


def test_pick_falls_back_when_the_key_is_unknown(project):
    found = compare.references(project, "s01_pub", "sh01")
    assert compare.pick(found, "nonsense", 0) is found[0]
    assert compare.pick(found, found[-1].key, 0) is found[-1]
    assert compare.pick([], None, 0) is None


def test_grouped_keeps_the_order_the_references_came_in(project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    groups = compare.grouped(compare.references(project, "s01_pub", "sh01"))
    assert list(groups) == ["This shot", "Identity"]


# -- the view ---------------------------------------------------------------


def test_the_view_shows_two_images_at_once(client, project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    page = client.get("/shot/s01_pub/sh01/compare").text
    assert page.count("compare-pane") >= 2
    assert "Side by side" in page


def test_the_two_can_be_chosen(client, project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    page = client.get("/shot/s01_pub/sh01/compare?left=blockout&right=take:1").text
    assert "blockout.png" in page
    assert "first_v1.png" in page


def test_a_shot_with_nothing_to_show_says_so(client, project):
    project.paths.blockout("s01_pub", "sh01").unlink()
    for character in project.shot("s01_pub", "sh01").characters:
        project.paths.sheet("character", character).unlink()
    assert client.get("/shot/s01_pub/sh01/compare").status_code == 404


def test_a_missing_shot_is_a_404(client):
    assert client.get("/shot/s01_pub/sh99/compare").status_code == 404


def test_the_take_tile_offers_the_comparison(client, project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("a.png"))
    takes = client.get("/shot/s01_pub/sh01/region/see").text
    assert "/compare?left=take:1" in takes
