import dbm
import logging
import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from ploc.type_defs import ModuleInterface, ModuleLocation, ModulePath, NameImport

CACHE_DIR = ".ploc_cache"
DBM_FILE = "cache.dbm"


class CachedModuleInterface(BaseModel, frozen=True):
    imported_names: dict[str, NameImport]
    exported_names: set[str]
    submodules: set[str]
    timestamp: float


@contextmanager
def _open_dbm_cache(dir: Path) -> Iterator[MutableMapping[str | bytes, bytes]]:
    cache_dir = dir.resolve() / CACHE_DIR
    if not cache_dir.exists():
        cache_dir.mkdir()
        with open(cache_dir / ".gitignore", "w") as fh:
            fh.write("*\n")

    cache_db = cache_dir / DBM_FILE
    if dbm.whichdb(cache_db) == "":  # "" = file exists but is not a DBM
        logging.warning(f"Invalid DB file, deleting: {cache_db}")
        cache_db.unlink()

    with dbm.open(cache_db, "c") as cache:  # "c" = create if not existing
        yield cache


def _key(module_path: ModulePath) -> bytes:
    return b".".join(comp.encode() for comp in module_path)


class PlocInterfacesCachePort(Protocol):
    def get_interface(self, loc: ModuleLocation) -> ModuleInterface | None: ...

    def set_interface(self, interface: ModuleInterface) -> None: ...


class _DbmCache(PlocInterfacesCachePort):
    def __init__(self, dbm_cache: MutableMapping[str | bytes, bytes]) -> None:
        self._dbm_cache = dbm_cache

    def get_interface(self, loc: ModuleLocation) -> ModuleInterface | None:
        try:
            data = self._dbm_cache[_key(loc.path)]
        except KeyError:
            return None
        try:
            value = CachedModuleInterface.model_validate_json(data)
        except ValidationError:
            logging.warning(f"Invalid cache entry, deleting: {loc.path}")
            del self._dbm_cache[_key(loc.path)]
            return None

        if value.timestamp >= loc.file.stat().st_mtime:
            # Cached interface more recent than file -> valid
            return ModuleInterface(
                location=loc,
                imported_names=value.imported_names,
                exported_names=value.exported_names,
                submodules=value.submodules,
            )
        else:
            return None

    def set_interface(self, interface: ModuleInterface) -> None:
        value = CachedModuleInterface(
            imported_names=interface.imported_names,
            exported_names=interface.exported_names,
            submodules=interface.submodules,
            timestamp=time.time(),
        )
        self._dbm_cache[_key(interface.location.path)] = value.model_dump_json().encode()


class _NoCacheCache(PlocInterfacesCachePort):
    def get_interface(self, loc: ModuleLocation) -> ModuleInterface | None:
        return None

    def set_interface(self, interface: ModuleInterface) -> None:
        pass


@contextmanager
def ploc_interfaces_cache(
    dir: Path, cache_enabled: Literal["on", "off", "rebuild"]
) -> Iterator[PlocInterfacesCachePort]:
    if cache_enabled == "off":
        yield _NoCacheCache()
        return

    with _open_dbm_cache(dir) as dbm_cache:
        if cache_enabled == "rebuild":
            dbm_cache.clear()
        yield _DbmCache(dbm_cache)
