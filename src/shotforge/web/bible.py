"""Bible pages and form actions for the design stage."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import TemplateError

from shotforge import design, inbox
from shotforge.pipeline import TransitionError
from shotforge.project import KINDS, Project, ProjectError
from shotforge.skills.registry import SkillError
from shotforge.state import ProjectState
from shotforge.takes import TakeKind
from shotforge.tools import ToolError
from shotforge.yamlio import ProjectFileError

STARTER_FIELDS = {
    "character": "outfit: {}",
    "location": "set_dressing: []\nfixed_props: []\nlighting: null",
    "prop": "description: null\nstates: []",
}


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise HTTPException(404, f"unknown bible kind {kind!r}")


def _entity(project: Project, kind: str, entity_id: str):
    try:
        return project.entity(kind, entity_id)
    except ProjectError as exc:
        raise HTTPException(404, str(exc)) from exc


def _entity_url(kind: str, entity_id: str) -> str:
    return f"/bible/{kind}/{entity_id}"


def register_bible(
    app: FastAPI,
    load: Callable[[], tuple[Project, ProjectState]],
    render: Callable[..., HTMLResponse],
) -> None:
    @app.get("/bible", response_class=HTMLResponse)
    def bible(request: Request) -> HTMLResponse:
        project, state = load()
        groups = []
        for kind in KINDS:
            items = []
            for entity_id, entity in project.entities(kind).items():
                approved = state.approved_design_revision(kind, entity_id)
                items.append(
                    {
                        "id": entity_id,
                        "entity": entity,
                        "sheet": project.paths.sheet(kind, entity_id),
                        "designed": bool(
                            approved
                            and approved == design.revision(project, kind, entity_id, entity)
                        ),
                    }
                )
            groups.append({"kind": kind, "entities": items})
        return render(
            request,
            "bible.html",
            {"project": project, "kinds": groups, "style": project.style(), "page": "bible"},
        )

    def editor(
        request: Request,
        project: Project,
        kind: str,
        source: str,
        entity_id: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        response = render(
            request,
            "bible_edit.html",
            {
                "project": project,
                "page": "bible",
                "kind": kind,
                "entity_id": entity_id,
                "source": source,
                "error": error,
            },
        )
        if error:
            response.status_code = 400
        return response

    @app.get("/bible/new/{kind}", response_class=HTMLResponse)
    def new_entity(request: Request, kind: str) -> HTMLResponse:
        _check_kind(kind)
        project, _ = load()
        source = (
            "id: ''\nnames:\n  book: ''\ntag: null\nfrom_book: []\ndesign_choices: []\n"
            f"{STARTER_FIELDS[kind]}\n"
        )
        return editor(request, project, kind, source)

    @app.post("/bible/new/{kind}", response_model=None)
    def create_entity(request: Request, kind: str, source: str = Form(...)):
        _check_kind(kind)
        project, state = load()
        try:
            entity = design.save_entity(project, state, kind, source)
        except (ProjectFileError, ProjectError) as exc:
            return editor(request, project, kind, source, error=str(exc))
        return RedirectResponse(_entity_url(kind, entity.id), status_code=303)

    def entity_card(
        request: Request,
        kind: str,
        entity_id: str,
        error: str | None = None,
    ) -> HTMLResponse:
        project, state = load()
        entity = _entity(project, kind, entity_id)
        revision = design.revision(project, kind, entity_id, entity)
        response = render(
            request,
            "bible_entity.html",
            {
                "project": project,
                "page": "bible",
                "kind": kind,
                "entity": entity,
                "url": _entity_url(kind, entity_id),
                "error": error,
                "revision": revision,
                "designed": revision is not None
                and revision == state.approved_design_revision(kind, entity_id),
                "assets": {
                    asset: asset.path(project.paths, kind, entity_id) for asset in inbox.DesignAsset
                },
                "prompts": design.current_prompts(project, kind, entity_id, entity),
                "inbox_images": [
                    item for item in inbox.scan(project) if item.kind is TakeKind.frame
                ],
            },
        )
        if error:
            response.status_code = 400
        return response

    @app.get("/bible/{kind}/{entity_id}", response_class=HTMLResponse)
    def entity_page(request: Request, kind: str, entity_id: str) -> HTMLResponse:
        return entity_card(request, kind, entity_id)

    @app.get("/bible/{kind}/{entity_id}/edit", response_class=HTMLResponse)
    def edit_entity(request: Request, kind: str, entity_id: str) -> HTMLResponse:
        project, _ = load()
        _entity(project, kind, entity_id)
        source = project.bible_entity_path(kind, entity_id).read_text(encoding="utf-8")
        return editor(request, project, kind, source, entity_id)

    @app.post("/bible/{kind}/{entity_id}/edit", response_model=None)
    def save_entity(request: Request, kind: str, entity_id: str, source: str = Form(...)):
        project, state = load()
        _entity(project, kind, entity_id)
        try:
            design.save_entity(project, state, kind, source, entity_id)
        except (ProjectFileError, ProjectError) as exc:
            return editor(request, project, kind, source, entity_id, str(exc))
        return RedirectResponse(_entity_url(kind, entity_id), status_code=303)

    @app.post("/bible/{kind}/{entity_id}/prompts", response_model=None)
    def generate_prompts(request: Request, kind: str, entity_id: str):
        project, _ = load()
        _entity(project, kind, entity_id)
        try:
            design.build_prompts(project, kind, entity_id)
        except (SkillError, TemplateError, ProjectFileError) as exc:
            return entity_card(request, kind, entity_id, str(exc))
        return RedirectResponse(f"{_entity_url(kind, entity_id)}#prompts", status_code=303)

    @app.post("/bible/{kind}/{entity_id}/{asset}/file", response_model=None)
    def file_asset(
        request: Request,
        kind: str,
        entity_id: str,
        asset: inbox.DesignAsset,
        file: UploadFile | None = None,
        name: str = Form(""),
    ):
        project, state = load()
        _entity(project, kind, entity_id)
        try:
            if file is not None and file.filename:
                with inbox.stage_upload(project, file.filename, file.file) as source:
                    design.file_asset(project, state, kind, entity_id, asset, source)
            elif name:
                design.file_asset(
                    project,
                    state,
                    kind,
                    entity_id,
                    asset,
                    project.paths.inbox / Path(name).name,
                )
            else:
                raise ValueError("Choose an image to file.")
        except (ValueError, FileNotFoundError, ToolError) as exc:
            return entity_card(request, kind, entity_id, str(exc))
        return RedirectResponse(f"{_entity_url(kind, entity_id)}#{asset.value}", status_code=303)

    @app.post("/bible/{kind}/{entity_id}/approve", response_model=None)
    def approve(
        request: Request,
        kind: str,
        entity_id: str,
        revision: str = Form(""),
        note: str = Form(""),
    ):
        project, state = load()
        _entity(project, kind, entity_id)
        try:
            design.approve(project, state, kind, entity_id, revision, note.strip() or None)
        except TransitionError as exc:
            return entity_card(request, kind, entity_id, str(exc))
        return RedirectResponse(f"{_entity_url(kind, entity_id)}#approval", status_code=303)
