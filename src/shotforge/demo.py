"""Copy the bundled public example into a writable project."""

from pathlib import Path
from shutil import copytree

from shotforge.project import Project, ProjectError

HERE = Path(__file__).resolve().parent


def copy_demo(target: Path) -> Project:
    source = HERE / "_demo"
    if not source.is_dir():
        source = HERE.parent.parent / "examples" / "last_light"
    target = target.expanduser().resolve()
    if target == source or source in target.parents:
        raise ProjectError("Choose a destination outside the bundled example.")
    try:
        copytree(source, target)
    except FileExistsError as exc:
        raise ProjectError(f"{target} already exists. Choose a new folder.") from exc
    return Project.open(target)
