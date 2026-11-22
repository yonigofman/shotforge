"""The cockpit: FastAPI, Jinja, HTMX, Alpine — on localhost, no build step.

The screens are thin: every change goes through :mod:`shotforge.actions`, the
same code the CLI uses, and the project folder stays the source of truth.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import FileResponse

from shotforge import actions, blender, checks, continuity, tools
from shotforge import inbox as inbox_mod
from shotforge.console import setup_console
from shotforge.models import ShotStatus, Stage
from shotforge.models.materials import Materials
from shotforge.pipeline import ORDER, STAGE_LABEL, TransitionError, next_action, reopen
from shotforge.project import KINDS, Project, ProjectError
from shotforge.skills import find_skill, latest_prompt
from shotforge.skills.registry import SkillError
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, add_take, approved_take_number, list_takes
from shotforge.yamlio import ProjectFileError

HERE = Path(__file__).resolve().parent


def _number(value: float | int) -> str:
    """15 rather than 15.0 — the same spelling as in the prompts."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)

STAGES: tuple[Stage, ...] = (Stage.breakdown, Stage.blockout, Stage.frame, Stage.video)


def create_app(project_root: Path) -> FastAPI:
    setup_console()
    project_root = Path(project_root).resolve()
    app = FastAPI(title="Shotforge cockpit", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["n"] = _number
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ---- helpers ---------------------------------------------------------
    def load() -> tuple[Project, ProjectState]:
        """Re-read the project on every request: files are the source of truth."""
        try:
            project = Project.open(project_root)
        except (ProjectError, ProjectFileError) as exc:
            raise HTTPException(500, str(exc)) from exc
        tools.configure(project.config.tool_paths.model_dump())
        return project, ProjectState(project.paths)

    def media_url(project: Project, path: Path) -> str:
        return f"/media/{project.paths.rel(path)}"

    def render(request: Request, name: str, context: dict[str, Any]) -> HTMLResponse:
        project: Project = context["project"]
        context.setdefault("stages", STAGES)
        context.setdefault("stage_label", STAGE_LABEL)
        context.setdefault("media_url", lambda path: media_url(project, path))
        context.setdefault("rel", project.paths.rel)
        context.setdefault("vendored", (HERE / "static" / "vendor" / "htmx.min.js").is_file())
        return templates.TemplateResponse(request, name, context)

    def materials_for_page(project: Project) -> tuple[Materials, str]:
        try:
            return project.materials(), ""
        except ProjectFileError as exc:
            return Materials({}), str(exc)

    def inbox_context(project: Project, state: ProjectState) -> dict[str, Any]:
        materials, error = materials_for_page(project)
        names = {Path(item.texture).stem for item in materials.root.values()}
        return {
            "project": project,
            "inbox": inbox_mod.scan(project),
            "active": state.active,
            "texture_targets": sorted(names),
            "material_error": error,
        }

    def shot_card_context(request: Request, scene_id: str, shot_id: str) -> dict[str, Any]:
        project, state = load()
        try:
            shot = project.shot(scene_id, shot_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        shot_paths = project.paths.shot(scene_id, shot_id)
        action = next_action(project, state, scene_id, shot_id)
        shot_state = state.shot(scene_id, shot_id)
        prompt_path = (
            latest_prompt(project, scene_id, shot_id, action.skill) if action.skill else None
        )
        frame_takes = list_takes(shot_paths, TakeKind.frame)
        video_takes = list_takes(shot_paths, TakeKind.video)
        scene = project.scene(scene_id)
        previous = project.previous_shot(scene_id, shot_id)
        return {
            "request": request,
            "project": project,
            "scene": scene,
            "shot": shot,
            "shot_paths": shot_paths,
            "blockout": project.paths.blockout(scene_id, shot_id),
            "action": action,
            "shot_state": shot_state,
            "stage_index": ORDER.index(shot.status),
            "prompt_text": prompt_path.read_text(encoding="utf-8") if prompt_path else None,
            "prompt_path": prompt_path,
            "frame_takes": frame_takes,
            "video_takes": video_takes,
            "approved_frame": approved_take_number(shot_paths, TakeKind.frame),
            "approved_video": approved_take_number(shot_paths, TakeKind.video),
            "findings": checks.check_spec(project, scene_id, shot),
            "cut_findings": continuity.shot_findings(project, scene, shot, previous),
            "previous": previous,
            "active": state.active == (scene_id, shot_id),
            "stage_label": STAGE_LABEL,
            "label": actions.stage_label(shot),
        }

    # ---- pages -----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        project, state = load()
        scenes = []
        for scene_id in project.scene_ids():
            shots = project.shots(scene_id)
            scenes.append(
                {
                    "scene": project.scene(scene_id),
                    "shots": shots,
                    "done": sum(1 for s in shots if s.status.stage is Stage.done),
                    "verdicts": checks.scene_summary(project, project.scene(scene_id)),
                }
            )
        return render(
            request,
            "index.html",
            {
                **inbox_context(project, state),
                "scenes": scenes,
                "log": state.approvals(limit=8),
                "page": "projects",
            },
        )

    @app.get("/scene/{scene_id}", response_class=HTMLResponse)
    def storyboard(request: Request, scene_id: str) -> HTMLResponse:
        project, state = load()
        try:
            scene = project.scene(scene_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        rows = []
        for shot in project.shots(scene_id):
            shot_paths = project.paths.shot(scene_id, shot.id)
            rows.append(
                {
                    "shot": shot,
                    "label": actions.stage_label(shot),
                    "action": next_action(project, state, scene_id, shot.id),
                    "blockout": project.paths.blockout(scene_id, shot.id),
                    "frame": shot_paths.approved_frame,
                    "video": shot_paths.approved_video,
                    "last_frame": shot_paths.last_frame,
                    "verdict": checks.summarise(checks.check_spec(project, scene_id, shot)),
                    "stage_index": ORDER.index(shot.status),
                }
            )
        return render(
            request,
            "storyboard.html",
            {
                "project": project,
                "scene": scene,
                "rows": rows,
                "findings": checks.check_scene_chain(project, scene_id)
                + continuity.scene_findings(project, scene),
                "active": state.active,
                "page": "storyboard",
            },
        )

    @app.post("/scene/{scene_id}/continuity", response_class=HTMLResponse)
    def review_continuity(request: Request, scene_id: str) -> HTMLResponse:
        project, _ = load()
        try:
            scene = project.scene(scene_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        findings = checks.check_scene_chain(project, scene_id)
        findings += continuity.scene_findings(project, scene, run=True)
        return render(
            request, "partials/_findings.html", {"project": project, "findings": findings}
        )

    @app.get("/shot/{scene_id}/{shot_id}", response_class=HTMLResponse)
    def shot_page(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        context = shot_card_context(request, scene_id, shot_id)
        context["page"] = "shot"
        return render(request, "shot.html", context)

    @app.get("/shot/{scene_id}/{shot_id}/card", response_class=HTMLResponse)
    def shot_card(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        return render(request, "partials/_card.html", shot_card_context(request, scene_id, shot_id))

    @app.get("/shot/{scene_id}/{shot_id}/takes", response_class=HTMLResponse)
    def shot_takes(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        context = shot_card_context(request, scene_id, shot_id)
        return render(request, "partials/_takes.html", context)

    @app.get("/bible", response_class=HTMLResponse)
    def bible(request: Request) -> HTMLResponse:
        project, _ = load()
        materials, error = materials_for_page(project)
        textures: dict[tuple[str, str], list[tuple[str, Path]]] = {}
        for surface, material in materials.root.items():
            origin = materials.source_for(surface)
            textures.setdefault((origin.kind, origin.id), []).append(
                (surface, project.paths.textures / material.texture)
            )
        kinds = []
        for kind in KINDS:
            entities = []
            for entity_id, entity in project.entities(kind).items():
                entities.append(
                    {
                        "entity": entity,
                        "sheet": project.paths.sheet(kind, entity_id),
                        "textures": textures.get((kind, entity_id), []),
                    }
                )
            kinds.append({"kind": kind, "entities": entities})
        return render(
            request,
            "bible.html",
            {
                "project": project, "kinds": kinds, "style": project.style(),
                "material_error": error, "page": "bible",
            },
        )

    @app.get("/log", response_class=HTMLResponse)
    def log_page(request: Request) -> HTMLResponse:
        project, state = load()
        return render(
            request,
            "log.html",
            {"project": project, "entries": state.approvals(limit=200), "page": "log"},
        )

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox_partial(request: Request) -> HTMLResponse:
        project, state = load()
        return render(
            request,
            "partials/_inbox.html",
            inbox_context(project, state),
        )

    # ---- actions ---------------------------------------------------------
    @app.post("/shot/{scene_id}/{shot_id}/prompt", response_class=HTMLResponse)
    def make_prompt(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        try:
            actions.build_prompt(project, state, scene_id, shot_id)
        except (ValueError, TransitionError, SkillError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/blockout", response_class=HTMLResponse)
    def render_blockout(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, _ = load()
        try:
            shot = project.shot(scene_id, shot_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        if shot.status is not ShotStatus.blockout:
            raise HTTPException(400, "Start the Blockout stage before rendering.")
        try:
            blender.render(scene_id, shot_id, project=project)
        except blender.BlockoutError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/begin", response_class=HTMLResponse)
    def begin_stage(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        try:
            actions.begin(project, state, scene_id, shot_id)
        except TransitionError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/pass2", response_class=HTMLResponse)
    def to_pass_two(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        try:
            actions.advance_frame_pass(project, state, scene_id, shot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/approve", response_class=HTMLResponse)
    def approve_stage(
        request: Request,
        scene_id: str,
        shot_id: str,
        take: int | None = Form(None),
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load()
        shot = project.shot(scene_id, shot_id)
        note = (note or "").strip() or None
        try:
            if shot.status.stage is Stage.frame:
                number = take or _latest(project, scene_id, shot_id, TakeKind.frame)
                actions.approve_frame(project, state, scene_id, shot_id, number, note)
            elif shot.status.stage is Stage.video:
                number = take or _latest(project, scene_id, shot_id, TakeKind.video)
                actions.approve_video(project, state, scene_id, shot_id, number, note)
            else:
                from shotforge.pipeline import approve

                approve(project, state, scene_id, shot_id, note=note)
        except (TransitionError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/reopen", response_class=HTMLResponse)
    def reopen_stage(
        request: Request,
        scene_id: str,
        shot_id: str,
        stage: str = Form(...),
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load()
        try:
            reopen(project, state, scene_id, shot_id, Stage(stage), note=(note or None))
        except (TransitionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/qa", response_class=HTMLResponse)
    def run_qa(
        request: Request,
        scene_id: str,
        shot_id: str,
        kind: str = Form("frame"),
        take: int | None = Form(None),
    ) -> HTMLResponse:
        project, _ = load()
        take_kind = TakeKind(kind)
        number = take or _latest(project, scene_id, shot_id, take_kind)
        try:
            findings = actions.run_qa(project, scene_id, shot_id, take_kind, number)
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc
        return render(
            request,
            "partials/_findings.html",
            {
                "project": project,
                "findings": findings,
                "verdict": checks.summarise(findings),
                "subject": f"{take_kind.value} v{number}",
            },
        )

    @app.post("/shot/{scene_id}/{shot_id}/active", response_class=HTMLResponse)
    def set_active(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        project.shot(scene_id, shot_id)
        state.set_active(scene_id, shot_id)
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/upload", response_class=HTMLResponse)
    async def upload(
        request: Request, scene_id: str, shot_id: str, files: list[UploadFile]
    ) -> HTMLResponse:
        project, _ = load()
        shot_paths = project.paths.shot(scene_id, shot_id)
        if not shot_paths.spec.is_file():
            raise HTTPException(404, f"no shot {shot_id} in {scene_id}")
        staging = project.paths.inbox
        staging.mkdir(parents=True, exist_ok=True)
        for upload_file in files:
            name = Path(upload_file.filename or "upload.bin").name
            temporary = staging / name
            temporary.write_bytes(await upload_file.read())
            kind = inbox_mod.classify(temporary)
            if kind is None:
                temporary.unlink(missing_ok=True)
                raise HTTPException(400, f"{name} is not an image or a video Shotforge files")
            add_take(shot_paths, kind, temporary, move=True)
        context = shot_card_context(request, scene_id, shot_id)
        return render(request, "partials/_takes.html", context)

    @app.post("/inbox/file", response_class=HTMLResponse)
    def file_from_inbox(
        request: Request,
        name: str = Form(...),
        scene: str | None = Form(None),
        shot: str | None = Form(None),
        texture: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load()
        source = project.paths.inbox / Path(name).name
        try:
            if texture:
                if scene or shot:
                    raise ValueError("choose a texture or a shot as the inbox destination")
                inbox_mod.file_to_texture(project, source, texture)
            else:
                target = (scene, shot) if scene and shot else state.active
                if target is None:
                    raise ValueError("no active shot — open a shot card first")
                inbox_mod.file_to_shot(project, source, *target)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return inbox_partial(request)

    # ---- media and events ------------------------------------------------
    @app.get("/media/{relative_path:path}")
    def media(relative_path: str) -> FileResponse:
        project, _ = load()
        target = (project.paths.root / relative_path).resolve()
        try:
            target.relative_to(project.paths.root)
        except ValueError as exc:
            raise HTTPException(403, "outside the project") from exc
        if not target.is_file():
            raise HTTPException(404, relative_path)
        return FileResponse(target)

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-sent events: the inbox and the approval log, as they change."""

        async def stream():
            last = None
            while True:
                if await request.is_disconnected():
                    break
                project, state = load()
                payload = {
                    "inbox": len(inbox_mod.scan(project)),
                    "approvals": len(state.approvals()),
                }
                if payload != last:
                    last = payload
                    yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/skills", response_class=HTMLResponse)
    def skills_page(request: Request) -> HTMLResponse:
        from shotforge.skills import list_skills

        project, _ = load()
        return render(
            request,
            "skills.html",
            {
                "project": project,
                "skills": list_skills(project.paths.skills),
                "page": "skills",
            },
        )

    @app.post("/skills/{name}/rules", response_class=HTMLResponse)
    def add_rule(request: Request, name: str, rule: str = Form(...)) -> HTMLResponse:
        """A fix that worked becomes a rule — on your say-so, never automatically."""
        project, _ = load()
        try:
            skill = find_skill(name, project.paths.skills)
            skill.add_rule(rule)
        except SkillError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/skills", status_code=303)

    def _latest(project: Project, scene_id: str, shot_id: str, kind: TakeKind) -> int:
        takes = list_takes(project.paths.shot(scene_id, shot_id), kind)
        if not takes:
            raise HTTPException(400, f"no {kind.value} take yet for {shot_id}")
        return takes[-1].number

    return app


def app_from_env() -> FastAPI:
    """Factory for ``uvicorn --reload``; the project folder comes from the env."""
    root = os.environ.get("SHOTFORGE_PROJECT")
    if not root:
        raise RuntimeError("set SHOTFORGE_PROJECT to the project folder")
    return create_app(Path(root))
