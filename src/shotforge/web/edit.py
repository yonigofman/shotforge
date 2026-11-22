"""The scene timeline and rough-cut handoff."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from starlette.responses import FileResponse

from shotforge import edit, tools
from shotforge.project import ProjectError
from shotforge.web.projects import project_prefix
from shotforge.yamlio import ProjectFileError


def register_edit(app: APIRouter, load, render) -> None:
    def cut_page(request: Request, scene_id: str, *, form=None, error=None, status=200):
        project, _ = load(request)
        try:
            shots = edit.timeline(project, scene_id)
            settings = edit.read_settings(project, scene_id)
        except (edit.EditError, ProjectError) as exc:
            raise HTTPException(404, str(exc)) from exc
        except ProjectFileError as exc:
            raise HTTPException(400, str(exc)) from exc
        frames = edit.current_cut(project, scene_id, settings, shots)
        durations = (
            [frames[pos] / project.config.fps for pos in range(len(shots))]
            if frames else [item.shot.duration_s for item in shots]
        )
        rows = []
        cursor = 0.0
        for item, duration in zip(shots, durations, strict=True):
            field = f"bridge-{item.shot.id}"
            bridge = settings.bridges.get(item.shot.id, 0)
            rows.append({
                "shot": item.shot, "start": cursor, "duration": duration,
                "problem": item.problem, "field": field,
                "bridge": form.get(field, bridge) if form is not None else bridge,
            })
            cursor += duration
        card_duration = frames[-1] / project.config.fps if frames else settings.end_seconds
        return render(
            request, "rough_cut.html", {
                "project": project, "scene_id": scene_id, "rows": rows,
                "ready": bool(rows) and all(not row["problem"] for row in rows),
                "assembled": frames is not None, "error": error,
                "total": cursor + card_duration, "card_start": cursor,
                "card_duration": card_duration,
                "end_title": form.get("end_title", "") if form is not None else settings.end_title,
                "end_seconds": (
                    form.get("end_seconds", "") if form is not None else settings.end_seconds
                ),
            },
            status_code=status,
        )

    @app.get("/scene/{scene_id}/cut", response_class=HTMLResponse)
    def rough_cut(request: Request, scene_id: str) -> HTMLResponse:
        return cut_page(request, scene_id)

    @app.post("/scene/{scene_id}/cut", response_class=HTMLResponse)
    async def assemble_cut(request: Request, scene_id: str):
        project, _ = load(request)
        form = await request.form()
        try:
            settings = edit.EditSettings(
                end_title=form.get("end_title"),
                end_seconds=form.get("end_seconds"),
                bridges={
                    key.removeprefix("bridge-"): value
                    for key, value in form.items() if key.startswith("bridge-")
                },
            )
            await run_in_threadpool(edit.assemble, project, scene_id, settings)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors()]
            return cut_page(request, scene_id, form=form, error=" ".join(errors), status=400)
        except (edit.EditError, ProjectError, ProjectFileError, tools.ToolError, OSError) as exc:
            return cut_page(request, scene_id, form=form, error=str(exc), status=400)
        return RedirectResponse(f"{project_prefix(request)}/scene/{scene_id}/cut", status_code=303)

    @app.get("/scene/{scene_id}/cut/{filename}")
    def cut_file(request: Request, scene_id: str, filename: str) -> FileResponse:
        if filename not in {edit.VIDEO, edit.EDL, edit.EXPORT}:
            raise HTTPException(404, "Unknown rough cut file.")
        project, _ = load(request)
        try:
            shots = edit.timeline(project, scene_id)
            settings = edit.read_settings(project, scene_id)
        except (edit.EditError, ProjectError, ProjectFileError) as exc:
            raise HTTPException(404, str(exc)) from exc
        if not edit.current_cut(project, scene_id, settings, shots):
            raise HTTPException(404, "Assemble the current cut first.")
        return FileResponse(
            project.paths.edit / filename,
            filename=filename if filename != edit.VIDEO else None,
            headers={"Cache-Control": "no-cache"},
        )
