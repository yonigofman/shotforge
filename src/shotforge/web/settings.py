"""Project settings for Claude Code, shared by the cockpit and CLI."""

from collections.abc import Callable
from typing import get_args

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from shotforge.claude_settings import ClaudeSettings, Effort
from shotforge.project import Project
from shotforge.state import ProjectState
from shotforge.web.projects import project_prefix
from shotforge.yamlio import ProjectFileError, dump


def register_settings(
    routes: APIRouter,
    load: Callable[[Request], tuple[Project, ProjectState]],
    render: Callable[..., HTMLResponse],
) -> None:
    def settings_page(
        request: Request, project: Project, values: dict, *,
        saved: bool = False, errors: list[str] | None = None,
    ) -> HTMLResponse:
        return render(
            request, "settings.html",
            {
                "project": project, "page": "settings", "values": values,
                "efforts": get_args(Effort), "saved": saved, "errors": errors,
            },
            status_code=400 if errors else 200,
        )

    @routes.get("/settings", response_class=HTMLResponse)
    def view_settings(request: Request, saved: bool = False) -> HTMLResponse:
        project, _ = load(request)
        try:
            settings = project.claude_settings()
        except (ProjectFileError, OSError) as exc:
            return settings_page(request, project, {}, errors=[str(exc)])
        values = settings.model_dump(exclude_none=True)
        values["fallback_models"] = ", ".join(settings.fallback_models)
        return settings_page(request, project, values, saved=saved)

    @routes.post("/settings", response_class=HTMLResponse)
    def save_settings(
        request: Request,
        model: str = Form(""),
        effort: str = Form(""),
        fallback_models: str = Form(""),
        timeout_s: str = Form(""),
    ):
        project, _ = load(request)
        values = {
            "model": model.strip(), "effort": effort, "fallback_models": fallback_models.strip(),
            "timeout_s": timeout_s.strip(),
        }
        try:
            settings = ClaudeSettings.model_validate({
                **values,
                "model": values["model"] or None,
                "effort": effort or None,
                "fallback_models": values["fallback_models"].split(",")
                if values["fallback_models"] else [],
                "timeout_s": values["timeout_s"] or None,
            })
            dump(project.paths.claude_settings, settings)
        except ValidationError as exc:
            errors = []
            for item in exc.errors():
                field = str(item["loc"][0]).replace("_", " ").capitalize()
                message = item["msg"]
                if item["type"] == "string_pattern_mismatch":
                    message = "Enter a Claude model alias or ID."
                errors.append(f"{field} — {message}")
            return settings_page(request, project, values, errors=errors)
        except OSError as exc:
            return settings_page(request, project, values, errors=[str(exc)])
        return RedirectResponse(f"{project_prefix(request)}/settings?saved=true", status_code=303)
