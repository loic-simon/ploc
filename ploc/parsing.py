from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from libcst import (
    AnnAssign,
    AsName,
    Assign,
    Attribute,
    BaseExpression,
    ClassDef,
    CSTTransformer,
    CSTVisitor,
    FlattenSentinel,
    FunctionDef,
    Import,
    ImportAlias,
    ImportFrom,
    ImportStar,
    Module,
    Name,
    TypeAlias,
    parse_module,
)

from ploc.type_defs import ModulePath, NameImport
from ploc.utils import groupby_sorted


def _get_export_name(node: BaseExpression) -> str:
    match node:
        case Name():
            return node.value
        case Attribute():
            return f"{_get_export_name(node.value)}.{node.attr}"
        case _:
            raise ValueError(f"Unexpected node: {node}")


def _alias_to_name_import(alias: ImportAlias, module_path: ModulePath) -> NameImport | None:
    imported_name = (alias.asname or alias).name
    match imported_name:
        case Name():
            return NameImport(
                module=module_path,
                export_name=_get_export_name(alias.name),
                import_name=imported_name.value,
            )
        case Attribute():
            return None  # Import x.y not relevant (does not assign to a name)
        case _:
            raise ValueError(f"strange import: {alias}")


def _name_import_to_alias(imp: NameImport) -> ImportAlias:
    return ImportAlias(
        name=Name(imp.export_name),
        asname=AsName(name=Name(imp.import_name)) if imp.import_name != imp.export_name else None,
    )


def _node_to_module_path(node: BaseExpression | None) -> ModulePath:
    match node:
        case None:
            return ()
        case Name() as name:
            return (name.value,)
        case Attribute() as attr:
            return (*_node_to_module_path(attr.value), attr.attr.value)
        case _:
            raise ValueError(f"Unexpected node: {node}")


def _module_path_to_node(path: ModulePath) -> Name | Attribute:
    assert path
    *rest, last = path
    return Attribute(value=_module_path_to_node(tuple(rest)), attr=Name(last)) if rest else Name(last)


class _PlocVisitor(CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self._imported_names = dict[str, NameImport]()
        self._exported_names = set[str]()

    def get_imported_names(self) -> dict[str, NameImport]:
        return self._imported_names

    def get_exported_names(self) -> set[str]:
        return self._exported_names

    def _import(self, alias: ImportAlias, module_path: ModulePath) -> None:
        name_import = _alias_to_name_import(alias, module_path)
        if name_import is not None:
            self._imported_names[name_import.import_name] = name_import

    def visit_Import(self, node: Import) -> bool | None:
        for alias in node.names:
            self._import(alias, ())
        return False

    def visit_ImportFrom(self, node: ImportFrom) -> bool | None:
        if not isinstance(node.names, ImportStar):
            for alias in node.names:
                self._import(alias, _node_to_module_path(node.module))
        return False

    def visit_ClassDef(self, node: ClassDef) -> bool | None:
        self._exported_names.add(node.name.value)
        return False

    def visit_FunctionDef(self, node: FunctionDef) -> bool | None:
        self._exported_names.add(node.name.value)
        return False

    def visit_Assign(self, node: Assign) -> bool | None:
        for target in node.targets:
            if isinstance(target.target, Name):
                self._exported_names.add(target.target.value)
        return False

    def visit_AnnAssign(self, node: AnnAssign) -> bool | None:
        if isinstance(node.target, Name):
            self._exported_names.add(node.target.value)
        return False

    def visit_TypeAlias(self, node: TypeAlias) -> bool | None:
        self._exported_names.add(node.name.value)
        return False


class _PlocTransformer(CSTTransformer):
    def __init__(self, imports_changes: Mapping[NameImport, NameImport]) -> None:
        super().__init__()
        self._imports_changes = dict(imports_changes)

    def assert_all_imports_replaced(self) -> None:
        assert not self._imports_changes, f"Remaining imports NOT replaced: {self._imports_changes}"

    class _FilteredImportNames(NamedTuple):
        to_keep: list[ImportAlias]
        to_create: set[NameImport]

    def _filter_import_names(self, names: Sequence[ImportAlias], *, module_path: ModulePath) -> _FilteredImportNames:
        indexes_to_remove = set[int]()
        imports_to_create = set[NameImport]()

        for index, alias in enumerate(names):
            name_import = _alias_to_name_import(alias, module_path)
            print("trying to replace", name_import)
            if name_import and (repl := self._imports_changes.pop(name_import, None)):
                assert (
                    repl.import_name == name_import.import_name
                ), f"Trying to replace an import that would change name binds: {name_import} -/> {repl}!"
                indexes_to_remove.add(index)
                imports_to_create.add(repl)

        return self._FilteredImportNames(
            to_keep=[name for i, name in enumerate(names) if i not in indexes_to_remove],
            to_create=imports_to_create,
        )

    def leave_Import(self, original_node: Import, updated_node: Import) -> Import | FlattenSentinel[Import]:
        filtered_names = self._filter_import_names(updated_node.names, module_path=())
        assert len(filtered_names.to_keep) + len(filtered_names.to_create) == len(updated_node.names)

        if not filtered_names.to_create:  # No changes for this node
            return updated_node

        remaining_node = updated_node.with_changes(names=filtered_names.to_keep) if filtered_names.to_keep else None
        new_node = Import(names=[_name_import_to_alias(imp) for imp in filtered_names.to_create])
        return FlattenSentinel((remaining_node, new_node)) if remaining_node else new_node

    def leave_ImportFrom(
        self, original_node: ImportFrom, updated_node: ImportFrom
    ) -> ImportFrom | FlattenSentinel[ImportFrom]:
        if isinstance(updated_node.names, ImportStar):
            return updated_node

        filtered_names = self._filter_import_names(
            updated_node.names, module_path=_node_to_module_path(updated_node.module)
        )
        assert len(filtered_names.to_keep) + len(filtered_names.to_create) == len(updated_node.names)

        if not filtered_names.to_create:  # No changes for this node
            return updated_node

        remaining_node = updated_node.with_changes(names=filtered_names.to_keep) if filtered_names.to_keep else None
        new_nodes = [
            ImportFrom(module=_module_path_to_node(path), names=[_name_import_to_alias(imp) for imp in _module_imps])
            for path, _module_imps in groupby_sorted(filtered_names.to_create, key=lambda imp: imp.module)
        ]
        return FlattenSentinel((remaining_node, *new_nodes) if remaining_node else new_nodes)


def _get_module_source(file: Path) -> Module:
    with open(file) as fh:
        module_source = fh.read()

    return parse_module(module_source)


def extract_module_imports_exports(file: Path) -> tuple[dict[str, NameImport], set[str]]:
    source_tree = _get_module_source(file)
    visitor = _PlocVisitor()
    source_tree.visit(visitor)
    return visitor.get_imported_names(), visitor.get_exported_names()


def replace_module_imports(file: Path, replacements: Mapping[NameImport, NameImport]) -> None:
    if not replacements:
        return

    source_tree = _get_module_source(file)
    transformer = _PlocTransformer(replacements)
    modified_tree = source_tree.visit(transformer)

    transformer.assert_all_imports_replaced()
    modified_source = modified_tree.code
    with open(file, "w") as fh:
        fh.write(modified_source)


if __name__ == "__main__":
    from argparse import ArgumentParser

    from pydantic import TypeAdapter, ValidationError
    from rich import print

    type _Repl = list[tuple[NameImport, NameImport]]

    def _glok[**P, R](call: Callable[P, R]) -> Callable[P, R]:
        def _inner(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return call(*args, **kwargs)
            except ValidationError as exc:
                raise Exception(exc)  # not catched by argparse

        return _inner

    parser = ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--repl", type=_glok(TypeAdapter(_Repl).validate_json))

    cli_args = parser.parse_args()
    file: Path = cli_args.file
    repl: _Repl | None = cli_args.repl

    info = extract_module_imports_exports(file)
    print(info)

    if repl:
        replace_module_imports(file, dict(repl))
        print("Imports replaced!")
