"""The folder tree, and reading the project back off disk."""

from __future__ import annotations

import pytest

from shotforge.paths import ProjectPaths
from shotforge.project import Project, ProjectError, slugify
from shotforge.yamlio import ProjectFileError


def test_new_project_has_the_fixed_tree(tmp_path):
    project = Project.create(tmp_path / "film", name="Film")
    paths = project.paths
    for folder in (
        paths.source,
        paths.bible,
        paths.bible_kind("character"),
        paths.design,
        paths.blender,
        paths.textures,
        paths.scenes,
        paths.edit,
        paths.inbox,
        paths.state_dir,
    ):
        assert folder.is_dir(), folder
    assert paths.config.is_file()
    assert paths.style.is_file()
    assert (paths.root / ".gitattributes").read_text().startswith("*.png filter=lfs")


def test_creating_twice_is_refused(tmp_path):
    Project.create(tmp_path / "film", name="Film")
    with pytest.raises(ProjectError, match="already"):
        Project.create(tmp_path / "film", name="Film")


def test_discover_walks_up_from_a_subfolder(project):
    found = ProjectPaths.find(project.paths.scenes / "s01_pub")
    assert found is not None and found.root == project.paths.root


def test_relative_paths_use_forward_slashes(project):
    assert project.paths.rel(project.paths.blockout("s01_pub", "sh01")) == (
        "03_blender/renders/s01_pub/sh01/blockout.png"
    )


def test_shots_are_ordered_and_linked(project):
    assert project.shot_ids("s01_pub") == ["sh01", "sh02"]
    assert project.previous_shot("s01_pub", "sh02").id == "sh01"
    assert project.previous_shot("s01_pub", "sh01") is None
    assert project.next_shot("s01_pub", "sh01").id == "sh02"


def test_a_broken_file_names_the_field(project):
    path = project.paths.shot("s01_pub", "sh01").spec
    path.write_text("id: sh01\nduration_s: -4\n", encoding="utf-8")
    with pytest.raises(ProjectFileError, match="duration_s"):
        project.shot("s01_pub", "sh01")
    assert project.validate()


def test_the_shipped_example_validates(example_project):
    assert example_project.validate() == []
    assert example_project.shot_ids("s01_pub") == ["sh06", "sh07", "sh08"]


def test_slugify_keeps_it_a_folder_name():
    assert slugify("Улыбка Вавилоны") == "улыбка_вавилоны"
    assert slugify("  ") == "project"
