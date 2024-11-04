from argparse import ArgumentParser
from collections import defaultdict
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal

from rich.progress import MofNCompleteColumn, Progress, SpinnerColumn

from ploc.caching import ploc_interfaces_cache
from ploc.config import PlocConfig
from ploc.parsing import extract_module_imports_exports
from ploc.printing import format_name_import
from ploc.type_defs import ModuleInterface, ModuleLocation, ModulePath, NameImport, is_subpath


def get_modules_locations(root: Path, root_module_path: ModulePath) -> Mapping[ModulePath, ModuleLocation]:
    locations = dict[ModulePath, ModuleLocation]()

    for file in root.rglob("*.py"):
        relative_module_path = tuple(file.relative_to(root).with_suffix("").as_posix().split("/"))
        module_path = root_module_path + relative_module_path

        if is_init := (relative_module_path[-1] == "__init__"):
            module_path = module_path[:-1]

        if module_path in locations:
            raise ValueError(f"Two files map to the same module path:\n  {file}\n  {locations[module_path]}")

        locations[module_path] = ModuleLocation(path=module_path, file=file, is_init=is_init)

    return locations


def get_modules_interfaces(
    root: Path, locations: Mapping[ModulePath, ModuleLocation], cache_enabled: Literal["on", "off", "rebuild"]
) -> Iterator[tuple[ModulePath, ModuleInterface]]:
    def _print_module(path: ModulePath) -> str:
        return f"\n\n    {".".join(path)} ({loc.file if (loc := locations.get(path)) else "<unknown location>"})"

    with ploc_interfaces_cache(root, cache_enabled) as cache:
        for loc in locations.values():
            if cached_interface := cache.get_interface(loc):
                yield loc.path, cached_interface
                continue

            imported_names, exported_names = extract_module_imports_exports(loc.file)

            if loc.is_init:
                # Modules in a package are "exported attributes" of __init__.py files
                submodules = {p[-1] for p in locations if is_subpath(loc.path, p)}
                if shadowed_attrs := submodules & exported_names:
                    raise ValueError(f"shadowed export attributes in {_print_module(loc.path)}:\n{shadowed_attrs}")
                imported_names = {
                    name: imp for name, imp in imported_names.items() if imp.export_name not in submodules
                }
            else:
                submodules = set[str]()

            interface = ModuleInterface(
                location=loc,
                imported_names=imported_names,
                exported_names=exported_names,
                submodules=submodules,
            )

            cache.set_interface(interface)
            yield loc.path, interface


def analyse_import(
    interfaces: Mapping[ModulePath, ModuleInterface],
    imp: NameImport,
) -> NameImport | None:
    try:
        src_module = interfaces[imp.module]
    except KeyError:
        raise ValueError(f"Referenced first-party module not found: {".".join(imp.module)}") from None

    original_imp = imp

    # Algorithm: unfold import chain as most as possible
    _module = src_module
    _name = imp.export_name
    while True:
        _original_imp = _module.imported_names.get(_name)
        if not _original_imp:
            break  # Name was not re-exported
        if _module.location.is_init and is_subpath(imp.module, _original_imp.module):
            return  # Re-exports from sub-modules are allowed in __init__ files

        # Name is a re-export: but can we simplify it further?
        original_imp = _original_imp
        _module = interfaces.get(original_imp.module)
        if not _module:
            break
        _name = original_imp.export_name

    if original_imp != imp:  # Name is a re-export
        return NameImport(
            module=original_imp.module,
            export_name=original_imp.export_name,
            import_name=imp.import_name,  # Keep original import name
        )

    if imp.export_name not in src_module.exported_names and imp.export_name not in src_module.submodules:
        raise ValueError(f"Imported member {imp.export_name!r} not found in {".".join(src_module.location.path)}")


def analyse_module_imports(
    root: Path, config: PlocConfig, cache_enabled: Literal["on", "off", "rebuild"] = "on"
) -> tuple[Mapping[ModuleLocation, Mapping[NameImport, NameImport]], int]:
    """Main PLOC logic: analyse a Python module to find indirect imports."""
    root = root.resolve()
    with Progress(SpinnerColumn(), *Progress.get_default_columns(), MofNCompleteColumn()) as progress:
        # Discover module files
        _locations = progress.add_task("Discovering modules...")
        locations = get_modules_locations(root, ())
        files_count = len(locations)
        progress.update(_locations, total=files_count, completed=files_count)

        # Extract imports and exports from all files
        interfaces = dict(
            progress.track(
                get_modules_interfaces(root, locations, cache_enabled),
                description="Extracting modules interfaces...",
                total=files_count,
            )
        )

        first_party_packages = {module_path[0] for module_path in interfaces if module_path}

        # Handle additional packages
        all_interfaces = interfaces
        for add_module, add_module_path in config.additional_packages.items():
            if add_module in first_party_packages:
                raise ValueError(
                    f"Module {add_module} was passed as additional_packages in configuration, "
                    f"but is a direct sub-module of {root}: conflict!"
                )
            _additional = progress.add_task(f"Extracting interfaces of additional module {add_module!r}...")
            add_module_locations = get_modules_locations(
                add_module_path,
                (add_module,),  # TODO: handle x.y additional modules?
            )
            progress.update(_additional, total=len(add_module_locations))
            add_module_interfaces = {
                path: progress.advance(_additional) or interface  # sorry
                for path, interface in get_modules_interfaces(add_module_path, add_module_locations, cache_enabled)
            }

            locations = {**locations, **add_module_locations}
            all_interfaces = {**all_interfaces, **add_module_interfaces}
            first_party_packages.add(add_module)

        # Analyse each file
        replacements = defaultdict[ModuleLocation, dict[NameImport, NameImport]](dict)
        for interface in progress.track(interfaces.values(), description="Analyzing all imports..."):
            for imp in interface.imported_names.values():
                if not imp.module or imp.module[0] not in first_party_packages:
                    continue

                try:
                    repl = analyse_import(all_interfaces, imp)
                except ValueError as exc:
                    raise ValueError(
                        f"Error when analyzing import in {interface.location.file}:\n\n"
                        f"    {format_name_import(imp)}\n\n"
                        f"Error: {exc}"
                    )

                if repl:
                    replacements[interface.location][imp] = repl

    return replacements, files_count


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("root", type=Path)
    cli_args = parser.parse_args()

    root: Path = cli_args.root
    analyse_module_imports(root, PlocConfig())
