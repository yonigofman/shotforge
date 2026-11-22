"""Skills render prompts from the project's own files, with no model involved."""

from __future__ import annotations

import pytest

from shotforge import actions
from shotforge.pipeline import approve
from shotforge.skills import find_skill, list_skills, render_prompt, shot_context
from shotforge.skills.registry import SkillError
from shotforge.skills.render import latest_prompt


def test_the_library_ships_the_three_mvp_skills():
    names = {skill.name for skill in list_skills()}
    assert {"chatgpt-first-frame", "chatgpt-identity-pass", "grok-video"} <= names


def test_a_project_skill_shadows_the_bundled_one(project):
    local = project.paths.skills / "grok-video"
    local.mkdir(parents=True, exist_ok=True)
    (local / "SKILL.md").write_text(
        "---\nname: grok-video\ntool: Grok\n---\n\nlocal override\n", encoding="utf-8"
    )
    skill = find_skill("grok-video", project.paths.skills)
    assert skill.root == local
    assert "local override" in skill.body


def test_an_unknown_skill_says_where_it_looked(project):
    with pytest.raises(SkillError, match="no skill named"):
        find_skill("does-not-exist", project.paths.skills)


def test_rules_keep_their_case_note_out_of_the_prompt():
    skill = find_skill("grok-video")
    assert any("(case:" in rule for rule in skill.rules())
    assert not any("(case:" in rule for rule in skill.constraints())


def test_adding_a_rule_is_idempotent(tmp_path, project):
    local = project.paths.skills / "grok-video"
    local.mkdir(parents=True, exist_ok=True)
    (local / "SKILL.md").write_text("---\nname: grok-video\n---\n", encoding="utf-8")
    skill = find_skill("grok-video", project.paths.skills)
    skill.add_rule("The mug is never refilled")
    skill.add_rule("The mug is never refilled")
    assert find_skill("grok-video", project.paths.skills).rules() == [
        "The mug is never refilled"
    ]


def test_the_video_prompt_carries_tags_timecodes_and_the_rules(project, state):
    skill = find_skill("grok-video")
    context = shot_context(project, "s01_pub", "sh02")
    text = render_prompt(skill, context)
    assert "@Durik" in text
    assert "0–5s" in text
    assert "completely static" in text
    assert "(case:" not in text


def test_the_first_frame_prompt_names_the_aspect_ratio_and_the_attachments(project, state):
    scene, shot_id = "s01_pub", "sh01"
    approve(project, state, scene, shot_id)
    actions.begin(project, state, scene, shot_id)
    approve(project, state, scene, shot_id)
    actions.begin(project, state, scene, shot_id)
    result = actions.build_prompt(project, state, scene, shot_id)
    assert result.skill.name == "chatgpt-first-frame"
    assert "Aspect ratio: 16:9" in result.text
    assert "blockout.png" in result.text
    assert result.path.name == "chatgpt-first-frame_v1.txt"
    again = actions.build_prompt(project, state, scene, shot_id)
    assert again.path.name == "chatgpt-first-frame_v2.txt"
    assert latest_prompt(project, scene, shot_id, "chatgpt-first-frame") == again.path


def test_a_prompt_is_refused_at_a_step_that_has_none(project, state):
    with pytest.raises(ValueError, match="no prompt"):
        actions.build_prompt(project, state, "s01_pub", "sh01")
