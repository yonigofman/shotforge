import json
import shutil

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shotforge.cli import app
from shotforge.models import Location, Scene, Shot, ShotStatus
from shotforge.project import Project
from shotforge.web.app import create_app


def local_client(registry, **kwargs):
    return TestClient(
        create_app(registry_path=registry, enforce_local=True),
        base_url="http://127.0.0.1:8765", client=("127.0.0.1", 51000), **kwargs,
    )


def test_create_open_and_switch_between_projects(tmp_path):
    registry = tmp_path / "registry.json"
    first = tmp_path / "Workspace One"
    second = Project.create(tmp_path / "two", name="Workspace Two")
    second.save_entity("location", Location(id="room"))
    second.save_scene(Scene(id="s01", location="room"))
    second.save_shot("s01", Shot(id="sh01", duration_s=1, status=ShotStatus.done))
    with local_client(registry) as client:
        assert "New project" in client.get("/").text
        assert "Choose a project" in client.get("/bible").text
        bad = client.post("/projects/open", data={"path": str(tmp_path)})
        assert bad.status_code == 400
        assert "holds no project.yaml" in bad.text

        response = client.post("/projects/new", data={
            "name": "Workspace One", "path": str(first), "language": "fr",
            "aspect": "9:16", "fps": "25", "media": "git",
        }, follow_redirects=False)
        assert response.status_code == 303
        one_url = response.headers["location"]
        assert one_url.startswith("/p/")
        one = Project.open(first)
        assert (one.config.language, one.config.aspect_ratio, one.config.fps) == ("fr", "9:16", 25)
        assert one.config.media == "git"
        assert one.paths.source.joinpath("ch01.md").is_file()
        duplicate = client.post("/projects/new", data={"name": "Other", "path": str(first)})
        assert duplicate.status_code == 400
        assert "Choose a new or empty folder" in duplicate.text
        assert Project.open(first).name == "Workspace One"

        response = client.post("/projects/open", data={"path": str(second.paths.root)},
                               follow_redirects=False)
        assert response.status_code == 303
        two_url = response.headers["location"]
        assert one_url != two_url
        projects = client.get("/projects")
        assert "0 / 0 shots done" in projects.text
        assert "1 / 1 shots done" in projects.text
        assert json.loads(registry.read_text())[0] == {
            "name": "Workspace Two", "path": str(second.paths.root),
        }

        # Each tab's links and writes stay with the project named by its URL.
        response = client.post(one_url + "bible/new/location", data={"source": "id: room\n"},
                               follow_redirects=False)
        assert response.headers["location"] == one_url + "bible/location/room"
        assert one.paths.entity_file("location", "room").is_file()
        shot = client.get(two_url + "shot/s01/sh01")
        assert f'hx-get="{two_url}shot/s01/sh01/card"' in shot.text
        assert f'EventSource("{two_url}events")' in shot.text
        assert client.get(one_url + "shot/s01/sh01").status_code == 404

        marker = second.paths.root / "marker.txt"
        marker.write_text("Workspace Two only", encoding="utf-8")
        assert client.get(two_url + "media/marker.txt").text == "Workspace Two only"
        assert client.get(one_url + "media/marker.txt").status_code == 404
        assert client.post(two_url + "shot/s01/sh01/active").status_code == 200
        assert not one.paths.state_file.exists()

    shutil.rmtree(second.paths.root)
    with local_client(registry) as client:
        assert "Missing folder" in client.get("/projects").text
        assert client.get(two_url).status_code == 404
        assert client.get(one_url).status_code == 200


def test_project_registry_rejects_nonlocal_requests(tmp_path):
    registry = tmp_path / "registry.json"
    with local_client(registry) as client:
        form = {"name": "blocked", "path": str(tmp_path / "blocked")}
        bad_host = client.post("/projects/new", data=form, headers={"Host": "example.com"})
        assert bad_host.status_code == 403
        cross_site = client.post("/projects/new", data=form,
                                 headers={"Origin": "https://example.com"})
        assert cross_site.status_code == 403
        assert not (tmp_path / "blocked").exists()
        assert not registry.exists()
    with TestClient(
        create_app(registry_path=registry, enforce_local=True),
        base_url="http://127.0.0.1", client=("192.0.2.1", 50000),
    ) as client:
        assert client.get("/projects").status_code == 403


def test_serve_without_a_project_starts_the_project_picker(tmp_path, monkeypatch):
    import uvicorn

    monkeypatch.chdir(tmp_path)
    started = {}

    def run(asgi, **kwargs):
        started.update(app=asgi, **kwargs)

    monkeypatch.setattr(uvicorn, "run", run)
    result = CliRunner().invoke(app, ["serve"])
    assert result.exit_code == 0
    assert started["host"] == "127.0.0.1"
    with TestClient(
        started["app"], base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        assert "New project" in client.get("/").text
    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 2
    assert "requires a loopback address" in result.output
