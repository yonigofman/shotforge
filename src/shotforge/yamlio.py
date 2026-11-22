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
    return _mapping(path.read_text(encoding="utf-8"), path)


def load(path: Path, model: type[T]) -> T:
    """Load one YAML file and validate it, reporting the field that is wrong."""
    return parse(path.read_text(encoding="utf-8"), path, model)


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


def dump(path: Path, model: BaseModel) -> None:
    """Validate, then write. A file is never left half-written."""
    text = yaml.safe_dump(
        as_plain(model),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    write_text(path, text)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent, prefix=f".{path.name}-") as directory:
        temp = Path(directory) / path.name
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
