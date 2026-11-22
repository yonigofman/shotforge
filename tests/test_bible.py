"""Exercise entity design through the cockpit's browser form endpoints."""

from __future__ import annotations

import json
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from shotforge.project import Project
from shotforge.state import ProjectState
from shotforge.web.app import create_app


@pytest.fixture
def bible(tmp_path):
    project = Project.create(tmp_path / "bible")
    return TestClient(create_app(project.paths.root)), project


def _revision(html: str) -> str:
    return re.search(r'name="revision" value="([a-f0-9]+)"', html)[1]


def test_design_a_character_from_name_through_approval(bible, png):
    client, project = bible
    assert "New character" in client.get("/bible").text
    page = "/bible/character/vera"
    source = "id: vera\nnames: {book: Вера}\ntag: '@Vera'\n"
    assert client.post("/bible/new/character", data={"source": source}).url.path == page
    source += (
        "from_book: [waits by the door]\ndesign_choices: [cropped hair]\noutfit: {coat: grey}\n"
    )
    assert client.post(f"{page}/edit", data={"source": source}).status_code == 200
    spec = project.paths.entity_file("character", "vera")
    assert spec.read_text(encoding="utf-8") == source
    text = client.post(f"{page}/prompts").text
    for expected in (
        "Design sheet prompt",
        "Clean reference prompt",
        "SOURCE FACTS FROM THE BOOK",
        "waits by the door",
        "PRODUCTION DESIGN CHOICES",
        "cropped hair",
        "coat: grey",
        "@Vera",
    ):
        assert expected in text

    assert client.post(f"{page}/approve").status_code == 400
    sheet = png("sheet.png", 64, 64).read_bytes()
    result = client.post(f"{page}/sheet/file", files={"file": ("image.png", sheet, "image/png")})
    assert result.status_code == 200
    assert project.paths.sheet("character", "vera").read_bytes() == sheet
    assert client.post(f"{page}/approve").status_code == 400

    downloaded = project.paths.inbox / "clean.png"
    downloaded.write_bytes(png("clean.png", 32, 32).read_bytes())
    result = client.post(f"{page}/ref/file", data={"name": downloaded.name})
    assert result.status_code == 200
    assert project.paths.ref("character", "vera").is_file()
    assert not downloaded.exists()

    # Filing both images leaves the approval gate open.
    assert ProjectState(project.paths).approved_design_revision("character", "vera") is None
    revision = _revision(result.text)
    result = client.post(f"{page}/approve", data={"revision": revision, "note": "Use this"})
    assert "Sheet and reference approved." in result.text
    client.post(f"{page}/approve", data={"revision": revision})
    state = json.loads(project.paths.state_file.read_text(encoding="utf-8"))
    assert state["designs"]["character/vera"]["designed"] is True
    assert spec.read_text(encoding="utf-8") == source
    entries = ProjectState(project.paths).approvals()
    assert [(entry.subject, entry.note) for entry in entries] == [("character/vera", "Use this")]
    assert page in client.get("/log").text

    new_source = source.replace("cropped hair", "long hair")
    result = client.post(f"{page}/edit", data={"source": new_source})
    assert "In design" in result.text
    assert "Design sheet prompt" not in result.text
    assert ProjectState(project.paths).approved_design_revision("character", "vera") is None
    assert client.post(f"{page}/approve", data={"revision": revision}).status_code == 400


@pytest.mark.parametrize(
    ("kind", "fields", "brief"),
    [
        ("location", "set_dressing: [linen curtains]\nlighting: amber\n", "linen curtains"),
        ("prop", "description: a chipped pitcher\nstates: [empty, full]\n", "empty, full"),
    ],
)
def test_other_entity_kinds_generate_their_own_prompts(bible, kind, fields, brief):
    client, _ = bible
    assert client.get(f"/bible/new/{kind}").status_code == 200
    source = f"id: new\n{fields}"
    page = f"/bible/{kind}/new"
    assert client.post(f"/bible/new/{kind}", data={"source": source}).url.path == page
    assert brief in client.post(f"{page}/prompts").text


def test_editor_shows_schema_errors_and_keeps_the_original(bible):
    client, project = bible
    source = "# A production note\nid: key\ndescription: oxidized brass\n"
    page = "/bible/prop/key"
    assert client.post("/bible/new/prop", data={"source": source}).status_code == 200
    assert "already exists" in client.post("/bible/new/prop", data={"source": source}).text
    assert "A production note" in client.get(f"{page}/edit").text
    bad = source + "descripton: green\n"
    result = client.post(f"{page}/edit", data={"source": bad})
    assert result.status_code == 400
    assert "descripton: Extra inputs are not permitted" in result.text
    assert "descripton: green" in result.text
    assert client.post(f"{page}/edit", data={"source": source + "states: ["}).status_code == 400
    assert client.post(f"{page}/edit", data={"source": "id: other"}).status_code == 400
    assert project.paths.entity_file("prop", "key").read_text(encoding="utf-8") == source
    assert not project.paths.entity_file("prop", "other").exists()


def test_inbox_filing_preserves_downloads_on_failure_and_checks_the_reviewed_images(bible, png):
    client, project = bible
    page = "/bible/prop/key"
    client.post("/bible/new/prop", data={"source": "id: key"})
    original = png("image.png", 64, 64).read_bytes()
    (project.paths.inbox / "image.png").write_bytes(original)
    bad = {"file": ("image.png", b"not a png", "image/png")}
    assert client.post(f"{page}/sheet/file", files=bad).status_code == 400
    assert (project.paths.inbox / "image.png").read_bytes() == original
    assert not project.paths.sheet("prop", "key").exists()

    assert client.post(f"{page}/sheet/file", data={"name": "image.png"}).status_code == 200
    image = {"file": ("image.png", original, "image/png")}
    revision = _revision(client.post(f"{page}/ref/file", files=image).text)
    assert client.post(f"{page}/approve", data={"revision": revision}).status_code == 200

    source = png("replace.png", 32, 32)
    jpeg = source.with_suffix(".jpeg")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(source), "-threads", "1", str(jpeg)],
        check=True,
    )
    replaced = client.post(
        f"{page}/ref/file", files={"file": ("replace.jpeg", jpeg.read_bytes(), "image/jpeg")}
    )
    assert replaced.status_code == 200
    assert "In design" in replaced.text
    assert project.paths.ref("prop", "key").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert client.post(f"{page}/approve", data={"revision": revision}).status_code == 400
    approved = client.post(f"{page}/approve", data={"revision": _revision(replaced.text)})
    assert approved.status_code == 200
