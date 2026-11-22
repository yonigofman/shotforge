from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge import design
from shotforge.cli import app
from shotforge.project import Project
from shotforge.state import ProjectState
from shotforge.web.app import create_app


def test_texture_prompt_requires_the_approved_sheet_and_describes_the_assignment(project, png):
    runner = CliRunner()
    command = [
        "texture-prompt", "location.pub.wall", "plaster", "--uv-scale", "1.5",
        "-C", str(project.paths.root),
    ]
    assert runner.invoke(app, command).exit_code == 2
    sheet = project.paths.sheet("location", "pub")
    sheet.write_bytes(png(width=32, height=32).read_bytes())
    ref = project.paths.ref("location", "pub")
    ref.write_bytes(png("reference.png", width=32, height=32).read_bytes())

    pending = runner.invoke(app, command)
    assert pending.exit_code == 2
    assert "approve the current design" in pending.output
    revision = design.revision(project, "location", "pub", project.location("pub"))
    assert revision is not None
    design.approve(project, ProjectState(project.paths), "location", "pub", revision)
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert f"Attach {project.paths.rel(sheet)}" in result.output
    assert "seamless square PNG tile" in result.output
    assert "location.pub.wall:\n  texture: plaster.png\n  uv_scale: 1.5" in result.output
    sheet.write_bytes(png("replacement.png", width=48, height=48).read_bytes())
    assert runner.invoke(app, command).exit_code == 2


def test_texture_takes_route_through_cli_and_cockpit_and_show_on_source_card(project, png):
    project.paths.materials.write_text(
        "location.pub.table:\n"
        "  texture: cloth.png\n"
        "  uv_scale: 2\n"
        "  source: {kind: character, id: dima}\n",
        encoding="utf-8",
    )
    first = png("first.png", width=32, height=32).read_bytes()
    second = png("second.png", width=64, height=64).read_bytes()
    (project.paths.inbox / "first.png").write_bytes(first)
    result = CliRunner().invoke(app, [
        "inbox", "file", "first.png", "--texture", "cloth", "-C", str(project.paths.root),
    ])
    assert result.exit_code == 0, result.output
    active = project.paths.textures / "cloth.png"
    assert active.read_bytes() == first
    assert not (project.paths.inbox / "first.png").exists()

    client = TestClient(create_app(project.paths.root))
    (project.paths.inbox / "bad.png").write_text("not a PNG", encoding="utf-8")
    invalid = client.post("/inbox/file", data={"name": "bad.png", "texture": "cloth"})
    assert invalid.status_code == 400
    assert active.read_bytes() == first
    assert (project.paths.inbox / "bad.png").exists()
    (project.paths.inbox / "second.png").write_bytes(second)
    assert "File as texture" in client.get("/inbox").text
    assert client.post(
        "/inbox/file", data={"name": "second.png", "texture": "cloth"},
    ).status_code == 200
    takes = project.paths.textures / "takes" / "cloth"
    assert (takes / "v1.png").read_bytes() == first
    assert (takes / "v2.png").read_bytes() == active.read_bytes() == second
    active.write_bytes(first)
    assert (takes / "v2.png").read_bytes() == second

    bible = client.get("/bible").text
    dima_card = next(
        card for card in bible.split("</article>") if '"/bible/character/dima"' in card
    )
    assert "02_design/characters/dima/sheet.png" in dima_card
    assert "03_blender/textures/cloth.png" in dima_card
    assert "location.pub.table" in dima_card
    assert client.get("/media/03_blender/textures/cloth.png").content == first


def test_invalid_material_assignment_is_reported(project: Project):
    project.paths.materials.write_text(
        "location.pub.wall: {texture: ../unsafe.png, uv_scale: 0}\n",
        encoding="utf-8",
    )
    errors = "\n".join(project.validate())
    assert "materials.yaml" in errors
    assert "png filename" in errors.lower()
    assert "greater than 0" in errors
