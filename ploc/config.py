import tomllib
from pathlib import Path
from typing import Annotated, TypedDict, cast

from pydantic import BaseModel, Field, ValidationError, ValidationInfo
from pydantic.types import PathType
from rich.console import Console
from rich.markup import escape

CONSOLE = Console()


class _ConfigValidationContext(TypedDict):
    paths_relative_to: Path


class _RelativeToPathType(PathType):
    @classmethod
    def validate_directory(cls, path: Path, info: ValidationInfo) -> Path:  # pyright: ignore[reportIncompatibleMethodOverride]
        context = cast(_ConfigValidationContext, info.context)

        if not path.is_absolute():
            path = context["paths_relative_to"] / path

        return super().validate_directory(path, info)


class PlocConfig(BaseModel, extra="forbid"):
    additional_packages: dict[str, Annotated[Path, _RelativeToPathType("dir")]] = Field(default_factory=dict)


class _PyprojectToolTable(BaseModel):
    ploc: PlocConfig = Field(default_factory=PlocConfig)


class _PyprojectStructure(BaseModel):
    tool: _PyprojectToolTable = Field(default_factory=_PyprojectToolTable)


def locate_pyproject(dir: Path) -> Path | None:
    pyproject_file = (dir / "pyproject.toml").resolve()
    return pyproject_file if pyproject_file.exists() and pyproject_file.is_file() else None


ERROR_MESSAGES = {
    "extra_forbidden": "Unknown configuration key",
    "dict_type": "Value must be a TOML table",
    "path_type": "Value must be a valid path",
}


def _report_toml_parsing_error(pyproject_file: Path, exc: Exception) -> None:
    CONSOLE.print(":boom: Could not parse pyproject.toml file!", style="bold red")
    CONSOLE.print(f"   Trying to parse: {pyproject_file}", style="bright_black")
    CONSOLE.print(f"   Got {type(exc).__name__}: {exc}", style="bright_black")


def _report_config_validation_errors(pyproject_file: Path, exc: ValidationError) -> None:
    CONSOLE.print(
        f":boom: {exc.error_count()} error(s) found in pyproject.toml configuration:",
        style="bold red",
        highlight=False,
    )
    CONSOLE.print(f"   Source file: {pyproject_file}", style="bright_black")
    for error in exc.errors():
        loc = ".".join(str(p) for p in error["loc"])
        message = escape(ERROR_MESSAGES.get(error["type"], f'{error["msg"]} [{error["type"]}]'))
        _input = error["input"]
        value = escape(_input) if isinstance(_input, str) else _input
        CONSOLE.print(f"   * {loc}: {message} (got: {value!r})", style="red")


def config_from_pyproject(pyproject_file: Path) -> PlocConfig:
    """Read PLOC configuration from a pyproject.toml file."""
    pyproject_file = pyproject_file.resolve()
    try:
        with open(pyproject_file, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        _report_toml_parsing_error(pyproject_file, exc)
        raise SystemExit(1)

    try:
        pyproject = _PyprojectStructure.model_validate(
            data,
            context=_ConfigValidationContext(paths_relative_to=pyproject_file.parent),
        )
    except ValidationError as exc:
        _report_config_validation_errors(pyproject_file, exc)
        raise SystemExit(1)

    return pyproject.tool.ploc


def default_config() -> PlocConfig:
    return PlocConfig()
