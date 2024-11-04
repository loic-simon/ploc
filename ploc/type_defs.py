from pathlib import Path

from pydantic import BaseModel

type ModulePath = tuple[str, ...]


def is_subpath(small: ModulePath, big: ModulePath) -> bool:
    _sub_len = len(small)
    return len(big) == _sub_len + 1 and big[:_sub_len] == small


class ModuleLocation(BaseModel, frozen=True):
    path: ModulePath
    file: Path
    is_init: bool


class NameImport(BaseModel, frozen=True):
    module: ModulePath
    export_name: str
    import_name: str

    @property
    def as_name(self) -> str | None:
        return self.import_name if self.import_name != self.export_name else None


class ModuleInterface(BaseModel, frozen=True):
    location: ModuleLocation
    imported_names: dict[str, NameImport]
    exported_names: set[str]
    submodules: set[str]
