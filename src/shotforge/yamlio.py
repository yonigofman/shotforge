"""Reading and writing the project's YAML, always through a schema."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from shotforge.models.common import as_plain

T = TypeVar("T", bound=BaseModel)


class ProjectFileError(Exception):
    """A file in the project does not match its schema."""

    def __init__(self, path: Path, detail: str):
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def read_yaml(path: Path) -> dict:
    return _mapping(_read_text(path), path)


def load(path: Path, model: type[T]) -> T:
    """Load one YAML file and validate it, reporting the field that is wrong."""
    return parse(_read_text(path), path, model)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ProjectFileError(path, str(exc)) from exc


def parse(text: str, path: Path, model: type[T]) -> T:
    """Validate YAML from disk or from the browser using the same schema."""
    try:
        return model.model_validate(_mapping(text, path))
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            lines.append(f"{loc}: {err['msg']}")
        raise ProjectFileError(path, "; ".join(lines)) from exc


def _mapping(text: str, path: Path) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProjectFileError(path, str(exc)) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ProjectFileError(path, "expected a mapping at the top level")
    return data


def render_yaml(model: BaseModel) -> str:
    return yaml.safe_dump(
        as_plain(model),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def dump(path: Path, model: BaseModel, *, create_only: bool = False) -> None:
    write_text(path, render_yaml(model), create_only=create_only)


def write_text(path: Path, text: str, *, create_only: bool = False) -> None:
    """Write a whole file at once, optionally refusing to replace an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent, prefix=f".{path.name}-") as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(text, encoding="utf-8")
        if create_only:
            # Linking a complete file refuses an existing destination atomically.
            path.hardlink_to(temporary)
        else:
            temporary.replace(path)
