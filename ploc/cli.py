from pathlib import Path
from time import time
from typing import Annotated, Literal

import cyclopts
from rich import print
from rich.progress import track

from ploc.analysis import analyse_module_imports, get_modules_interfaces, get_modules_locations
from ploc.config import PlocConfig, config_from_pyproject, default_config, locate_pyproject
from ploc.parsing import replace_module_imports
from ploc.printing import report_replacements, report_replacements_done

ploc_app = cyclopts.App(name="ploc", help="Plic, plac? Plic, PLOC!")


def _get_config(dir: Path, config_file: Path | None) -> PlocConfig:
    if not config_file:
        config_file = locate_pyproject(dir)

    if config_file:
        config = config_from_pyproject(config_file)
    else:
        # TODO: provide (partial) config from CLI options?
        config = default_config()
    return config


@ploc_app.command()
def check(
    *dirs: Annotated[
        Path,
        cyclopts.Parameter(
            help="The directory to analyse.",
            validator=cyclopts.validators.Path(exists=True, file_okay=False),
        ),
    ],
    config: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="The location of a pyproject.toml file containing tool config (in a [[tool.ploc]] table).",
            validator=cyclopts.validators.Path(exists=True, dir_okay=False),
        ),
    ] = None,
    cache: Annotated[
        Literal["on", "off", "rebuild"],
        cyclopts.Parameter(
            help="Whether to cache modules interfaces between successive calls.",
        ),
    ] = "on",
) -> None:
    """Analyse indirect imports of a Python package."""
    for dir in dirs:
        t1 = time()
        ploc_config = _get_config(dir, config)

        replacements, files_count = analyse_module_imports(dir, ploc_config, cache)
        t2 = time()
        report_replacements(replacements, files_count, seconds=t2 - t1)

        if replacements:
            raise SystemExit(1)


@ploc_app.command()
def interface(
    file: Annotated[
        Path,
        cyclopts.Parameter(
            help="The file to get interface of.",
            validator=cyclopts.validators.Path(exists=True, dir_okay=False),
        ),
    ],
) -> None:
    """Debug helper: get the interface (imported and exported names) of a file."""
    locations = get_modules_locations(file.parent, root_module_path=())
    interfaces = dict(get_modules_interfaces(file, locations, cache_enabled="off"))
    print(interfaces[() if file.stem == "__init__" else (file.stem,)])


@ploc_app.command()
def fix(
    *dirs: Annotated[
        Path,
        cyclopts.Parameter(
            help="The directories to analyse & fix.",
            validator=cyclopts.validators.Path(exists=True, file_okay=False),
        ),
    ],
    config: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="The location of a pyproject.toml file containing tool config (in a [[tool.ploc]] table).",
            validator=cyclopts.validators.Path(exists=True, dir_okay=False),
        ),
    ] = None,
    cache: Annotated[
        Literal["on", "off", "rebuild"],
        cyclopts.Parameter(
            help="Whether to cache modules interfaces between successive calls.",
        ),
    ] = "on",
) -> None:
    """Reduce indirect imports of a Python package."""
    for dir in dirs:
        ploc_config = _get_config(dir, config)

        t1 = time()
        replacements, files_count = analyse_module_imports(dir, ploc_config, cache)
        t2 = time()

        report_replacements(replacements, files_count, seconds=t2 - t1)

        if not replacements:
            raise SystemExit(0)  # success!

        t3 = time()
        for location, loc_replacements in track(replacements.items(), "Reducing imports..."):
            replace_module_imports(location.file, loc_replacements)
        t4 = time()

        report_replacements_done(seconds=t4 - t3)
        raise SystemExit(1)
