from typer.testing import CliRunner

from shotforge import tools
from shotforge.cli import app
from shotforge.project import Project
from shotforge.yamlio import dump


def test_windows_blender_discovery_uses_newest_installed_version(tmp_path, monkeypatch):
    program_files = tmp_path / "Program Files"
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setattr(tools, "_platform_key", lambda: "win32")
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("SHOTFORGE_BLENDER", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    older = program_files / "Blender Foundation" / "Blender 5.9" / "blender.exe"
    newest = local / "Programs" / "Blender Foundation" / "Blender 5.10" / "blender.exe"
    incomplete = program_files / "Blender Foundation" / "Blender 6.0"
    for executable in (older, newest):
        executable.parent.mkdir(parents=True)
        executable.touch()
    incomplete.mkdir()

    assert tools.find_tool("blender", overrides={}) == newest
    assert tools.find_tool("blender", overrides={"blender": str(older)}) == older


def test_doctor_honours_project_tool_paths(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "film")
    executable = tmp_path / "Custom Blender" / "blender.exe"
    executable.parent.mkdir()
    executable.touch()
    project.config.tool_paths.blender = str(executable)
    dump(project.paths.config, project.config)
    monkeypatch.setattr(tools, "_version", lambda name, path: None)
    monkeypatch.setattr(tools, "REQUIRED", ())
    monkeypatch.setattr(tools, "OPTIONAL", ("blender",))
    monkeypatch.setattr(tools, "_OVERRIDES", tools.ContextVar("test_tool_paths", default=None))

    result = CliRunner().invoke(app, ["doctor", "-C", str(project.paths.root)])
    assert result.exit_code == 0, result.output
    assert str(executable) in result.output
