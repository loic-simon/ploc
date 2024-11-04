from collections.abc import Collection, Mapping

from rich import print
from rich.style import Style
from rich.text import Text

from ploc.type_defs import ModuleLocation, ModulePath, NameImport
from ploc.utils import groupby_sorted


def _from_clause(path: ModulePath) -> str:
    return f"from {'.'.join(path)} " if path else ""


def _as_clause(as_name: str | None) -> str:
    return f" as {as_name}" if as_name else ""


def format_name_import(imp: NameImport) -> str:
    return f"{_from_clause(imp.module)}import {imp.export_name}{_as_clause(imp.as_name)}"


def format_name_imports(imps: Collection[NameImport]) -> list[str]:
    lines = list[str]()
    for (path, as_name), _module_imps in groupby_sorted(imps, key=lambda imp: (imp.module, imp.as_name or "")):
        lines.append(
            f"{_from_clause(path)}import {", ".join(i.export_name for i in _module_imps)}{_as_clause(as_name)}"
        )
    return lines


def report_replacements(
    replacements: Mapping[ModuleLocation, Mapping[NameImport, NameImport]], files_count: int, seconds: float
) -> None:
    replacements_count = 0
    for location, module_repl in replacements.items():
        replacements_count += len(module_repl)
        txt = Text(f"{location.file}:\n", Style(color="cyan"))
        for old in format_name_imports(module_repl.keys()):
            txt.append(f"  - {old}\n", Style(color="red"))
        for new in format_name_imports(module_repl.values()):
            txt.append(f"  + {new}\n", Style(color="green"))

        print(txt)

    print(
        Text(
            f"{len(replacements)} indirect import(s) found in {files_count} file(s) in {seconds:.2}s.",
            Style(color="yellow"),
        )
    )


def report_replacements_done(seconds: float) -> None:
    print(Text(f"Indirect import(s) fixed in {seconds:.2}s.", Style(color="yellow")))
    print(Text("You may want to run your formatter / import sorter!", Style(color="bright_black")))
