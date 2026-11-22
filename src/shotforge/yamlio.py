"""Reading and writing the project's YAML, always through a schema."""

from __future__ import annotations

from pathlib import Path
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
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ProjectFileError(path, "expected a mapping at the top level")
    return data


def load(path: Path, model: type[T]) -> T:
    """Load one YAML file and validate it, reporting the field that is wrong."""
    try:
        return model.model_validate(read_yaml(path))
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            lines.append(f"{loc}: {err['msg']}")
        raise ProjectFileError(path, "; ".join(lines)) from exc


def dump(path: Path, model: BaseModel) -> None:
    """Validate, then write. A file is never left half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        as_plain(model),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
