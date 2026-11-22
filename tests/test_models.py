"""The schemas refuse what scene 1 taught us to refuse."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shotforge.models import ProjectConfig, Shot
from shotforge.models.common import as_plain
from shotforge.models.shot import Beat, Continuity


def test_silent_beat_cannot_have_a_speaker():
    with pytest.raises(ValidationError, match="silence"):
        Beat(t=(9, 11), speaker="durik", line="…", silence=True)


def test_a_beat_needs_some_content():
    with pytest.raises(ValidationError, match="empty"):
        Beat(t=(0, 2))


def test_a_line_needs_a_speaker():
    with pytest.raises(ValidationError, match="speaker"):
        Beat(t=(0, 2), line="Я сказал студентам")


def test_beats_may_not_overlap():
    with pytest.raises(ValidationError, match="overlaps"):
        Shot(
            id="sh01",
            duration_s=10,
            beats=[Beat(t=(0, 5), action="a"), Beat(t=(4, 8), action="b")],
        )


def test_beats_may_not_run_past_the_shot():
    with pytest.raises(ValidationError, match="beats run to"):
        Shot(id="sh01", duration_s=5, beats=[Beat(t=(0, 9), action="a")])


def test_line_overrun_is_measured_at_two_and_a_half_words_a_second():
    beat = Beat(t=(0, 2), speaker="durik", line="one two three four five six seven eight nine ten")
    assert beat.line_overruns() == 2.0
    assert Beat(t=(0, 10), speaker="durik", line="short line").line_overruns() == 0.0


def test_continuity_in_is_spelled_in_in_yaml():
    shot = Shot(id="sh07", duration_s=5, continuity={"in": {"beer": "half"}})
    assert shot.continuity.in_ == {"beer": "half"}
    assert as_plain(shot)["continuity"]["in"] == {"beer": "half"}


def test_continuity_round_trips_through_plain_data():
    continuity = Continuity(**{"in": {"mood": "smile"}, "out": {"mood": "laughing"}})
    assert as_plain(continuity) == {"in": {"mood": "smile"}, "out": {"mood": "laughing"}}


def test_unknown_keys_are_rejected_so_typos_surface():
    with pytest.raises(ValidationError):
        Shot(id="sh01", duration_s=5, duraton_s=5)


def test_ids_are_lowercase_with_underscores():
    with pytest.raises(ValidationError, match="lowercase"):
        Shot(id="SH-07", duration_s=5)


def test_aspect_ratio_must_look_like_a_ratio():
    with pytest.raises(ValidationError, match="16:9"):
        ProjectConfig(name="x", aspect_ratio="widescreen")
    assert ProjectConfig(name="x", aspect_ratio="4:3").aspect_tuple == (4, 3)
