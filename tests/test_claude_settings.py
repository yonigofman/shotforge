from pathlib import Path
from subprocess import CompletedProcess

from fastapi.testclient import TestClient

from shotforge.engine import ClaudeCodeEngine
from shotforge.project import Project
from shotforge.registry import ProjectRegistry
from shotforge.skills import find_skill
from shotforge.web.app import create_app


def test_settings_save_for_one_project_and_apply_to_regular_claude_runs(tmp_path, monkeypatch):
    first = Project.create(tmp_path / "first", name="First film")
    second = Project.create(tmp_path / "second", name="Second film")
    before = first.paths.config.read_bytes()
    registry = ProjectRegistry(tmp_path / "registry.json")
    first_url = f"/p/{registry.remember(first).key}/settings"
    second_url = f"/p/{registry.remember(second).key}/settings"
    with TestClient(
        create_app(registry_path=registry.path),
        base_url="http://127.0.0.1", client=("127.0.0.1", 51000),
    ) as client:
        invalid = client.post(first_url, data={"model": "--tools", "timeout_s": "0"})
        assert invalid.status_code == 400
        assert "Enter a Claude model alias or ID." in invalid.text
        assert not first.paths.claude_settings.exists()

        saved = client.post(first_url, data={
            "model": "opus[1m]", "effort": "xhigh", "fallback_models": "sonnet, haiku",
            "timeout_s": "42",
        })
        assert saved.status_code == 200 and "Claude settings saved." in saved.text
        assert 'value="opus[1m]"' in client.get(first_url).text
        assert 'value="opus[1m]"' not in client.get(second_url).text
        assert not second.paths.claude_settings.exists()
        assert first.paths.config.read_bytes() == before

    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, '{"result": "{}"}', "")

    monkeypatch.setattr("shotforge.engine.claude_code.subprocess.run", run)
    skill = find_skill("book-breakdown")
    engine = ClaudeCodeEngine(executable=Path("claude"))
    assert engine.run_skill(skill, {}, cwd=first.paths.root).structured() == {}
    engine.run_skill(skill, {}, cwd=second.paths.root, files=[Path("reference.png")])

    command, options = calls[0]
    assert command[command.index("--model") + 1] == "opus[1m]"
    assert command[command.index("--effort") + 1] == "xhigh"
    assert command[command.index("--fallback-model") + 1] == "sonnet,haiku"
    assert options["timeout"] == 42
    default_command, default_options = calls[1]
    assert not {"--model", "--effort", "--fallback-model"}.intersection(default_command)
    assert default_options["timeout"] == 600


def test_invalid_settings_file_is_reported_and_can_be_corrected(project):
    path = project.paths.claude_settings
    path.write_text("effort: extraordinary\n", encoding="utf-8")
    assert any("claude.yaml" in error and "effort" in error for error in project.validate())
    with TestClient(create_app(project.paths.root)) as client:
        assert client.get("/settings").status_code == 400
        result = client.post("/settings", data={"effort": "low"})
        assert result.status_code == 200
        assert project.claude_settings().effort == "low"
        assert project.validate() == []
