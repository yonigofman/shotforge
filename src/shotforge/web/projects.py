"""Project list, directory entry and creation forms."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from filelock import Timeout
from pydantic import ValidationError

from shotforge.models import ShotStatus
from shotforge.models.common import ASPECT_RE
from shotforge.project import Project, ProjectError, slugify
from shotforge.registry import ProjectEntry, ProjectRegistry
from shotforge.yamlio import ProjectFileError

PROJECT_ERRORS = (OSError, ValueError, RuntimeError, ProjectError, ProjectFileError, Timeout)


def project_prefix(request: Request) -> str:
    key = request.path_params.get("project_id")
    return f"/p/{key}" if key else ""


def project_path(value: str) -> Path:
    if not value.strip():
        raise ProjectError("Enter the project folder path.")
    return Path(value.strip()).expanduser().resolve()


def project_row(entry: ProjectEntry) -> dict:
    row = {"entry": entry, "name": entry.name}
    if not entry.path.is_dir():
        return {**row, "status": "Missing folder"}
    try:
        project = Project.open(entry.path)
        shots = [shot for scene in project.scene_ids() for shot in project.shots(scene)]
    except PROJECT_ERRORS as exc:
        return {**row, "status": str(exc)}
    return {
        **row,
        "name": project.name,
        "done": sum(shot.status is ShotStatus.done for shot in shots),
        "total": len(shots),
    }


def register_projects(
    app: FastAPI, registry: ProjectRegistry, templates: Jinja2Templates, startup: Path | None,
) -> None:
    def render(request: Request, *, error=None, create_form=None, open_path="") -> HTMLResponse:
        rows = []
        registry_error = None
        try:
            entries = registry.entries()
            if startup and all(startup != entry.path for entry in entries):
                entries.insert(0, ProjectEntry(startup, startup.name))
            rows = [project_row(entry) for entry in entries]
        except PROJECT_ERRORS as exc:
            registry_error = f"Could not read the projects registry: {exc}"
        return templates.TemplateResponse(
            request, "projects.html", {
                "page": "projects",
                "rows": rows, "registry_error": registry_error, "error": error,
                "create_form": create_form or {}, "open_path": open_path, "cwd": Path.cwd(),
            },
            status_code=400 if error else 200,
        )

    def open_project(project: Project) -> RedirectResponse:
        entry = registry.remember(project)
        return RedirectResponse(f"/p/{entry.key}/", status_code=303)

    @app.get("/projects", response_class=HTMLResponse)
    def projects_page(request: Request) -> HTMLResponse:
        return render(request)

    @app.post("/projects/open", response_class=HTMLResponse)
    def use_project(
        request: Request, path: str = Form(""), key: str = Form(""),
    ):
        try:
            if key:
                entry = registry.find(key)
                if entry is None:
                    fallback = ProjectEntry(startup, startup.name) if startup else None
                    if fallback is None or fallback.key != key:
                        raise ProjectError("This project is not in the registry.")
                    entry = fallback
                root = entry.path
            else:
                root = project_path(path)
            if not root.is_dir():
                raise ProjectError(f"Folder does not exist or is not a directory: {root}")
            return open_project(Project.open(root))
        except PROJECT_ERRORS as exc:
            return render(request, error=str(exc), open_path=path)

    @app.post("/projects/new", response_class=HTMLResponse)
    def new_project(
        request: Request, name: str = Form(""), path: str = Form(""),
        language: str = Form("en"), aspect: str = Form("16:9"),
        fps: str = Form("24"), media: str = Form("lfs"),
    ):
        fields = {
            "name": name, "path": path, "language": language, "aspect": aspect,
            "fps": fps, "media": media,
        }
        try:
            name = name.strip()
            if not name:
                raise ProjectError("Enter a project name.")
            if not language.strip():
                raise ProjectError("Enter a dialogue language, for example en.")
            if media not in {"lfs", "external", "git"}:
                raise ProjectError("Choose Git LFS, an external folder, or plain Git.")
            aspect = aspect.strip()
            if not ASPECT_RE.fullmatch(aspect) or not all(map(int, aspect.split(":"))):
                raise ProjectError("Enter a positive aspect ratio, for example 16:9.")
            root = project_path(path or slugify(name))
            if root.exists() and (not root.is_dir() or any(root.iterdir())):
                raise ProjectError(f"Choose a new or empty folder: {root}")
            if not fps.strip().isdigit():
                raise ProjectError("FPS must be an integer between 1 and 120.")
            project = Project.create(
                root, name=name, language=language, aspect_ratio=aspect, fps=int(fps), media=media,
            )
            return open_project(project)
        except ValidationError as exc:
            messages = [item["msg"].removeprefix("Value error, ") for item in exc.errors()]
            return render(request, error=" ".join(messages), create_form=fields)
        except PROJECT_ERRORS as exc:
            return render(request, error=str(exc), create_form=fields)
