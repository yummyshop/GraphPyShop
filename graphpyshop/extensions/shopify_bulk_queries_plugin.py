import ast
import logging
from typing import Any, Dict, Optional, Tuple

from ariadne_codegen.codegen import (
    generate_import_from,
)
from ariadne_codegen.plugins.base import Plugin
from graphql import GraphQLSchema

class ShopifyBulkQueriesPlugin(Plugin):
    _ignore_args: set[str] = set()
    _ignore_args_full = {"self", *_ignore_args}

    def __init__(self, schema: GraphQLSchema, config_dict: Dict[str, Any]) -> None:
        super().__init__(schema=schema, config_dict=config_dict)
        self.imported_types: dict[str, str] = {}  # Track imported types
        logging.info("ShopifyBulkQueriesPlugin initialized with schema and config.")

    def generate_client_module(self, module: ast.Module) -> ast.Module:
        logging.info("Starting to generate client module.")
        self._collect_imports(module)
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                logging.info(f"Examining class: {node.name}")
                new_methods = self._enhance_class_with_bulk_methods(node, module)
                node.body.extend(new_methods)  # Append new methods after collecting all

        self._add_necessary_imports(module)
        self._flush_pending_imports(module)  # Ensure all pending imports are added
        ast.fix_missing_locations(module)
        return module

    def _collect_imports(self, module: ast.Module):
        for stmt in module.body:
            if isinstance(stmt, ast.ImportFrom):
                from_ = "." * stmt.level + (stmt.module or "")
                for alias in stmt.names:
                    self.imported_types[alias.name] = from_

    def _add_necessary_imports(self, module: ast.Module):
        # Ensure AsyncGenerator from typing is always imported
        if not any(
            isinstance(stmt, ast.ImportFrom)
            and stmt.module == "typing"
            and any(alias.name == "AsyncGenerator" for alias in stmt.names)
            for stmt in module.body
        ):
            module.body.insert(0, generate_import_from(["AsyncGenerator"], "typing"))

        # Ensure BulkOperationStatus from .enums is always imported
        if not any(
            isinstance(stmt, ast.ImportFrom)
            and stmt.module == ".enums"
            and any(alias.name == "BulkOperationStatus" for alias in stmt.names)
            for stmt in module.body
        ):
            bulk_operation_status_import = generate_import_from(
                names=["BulkOperationStatus"],
                from_=".enums",
            )
            module.body.insert(0, bulk_operation_status_import)

    def _enhance_class_with_bulk_methods(
        self, class_def: ast.ClassDef, module: ast.Module
    ):
        new_methods = []
        existing_method_names = {method.name for method in class_def.body if isinstance(method, ast.FunctionDef)}

        for method in class_def.body:
            if isinstance(method, ast.AsyncFunctionDef):
                #logging.info(f"Found async function: {method.name}")
                bulk_method_name = f"bq_{method.name}"
                if bulk_method_name in existing_method_names:
                    logging.info(f"Skipping bulk method creation for already existing method: {bulk_method_name}")
                    continue
                existing_method_names.add(bulk_method_name)
                # Skip if the method is already a bulk method
                if method.name.startswith("bq_"):
                    logging.info(
                        f"Skipping bulk method creation for already enhanced method: {method.name}"
                    )
                    continue
                if method.returns is None:
                    logging.info(
                        f"Skipping bulk method creation for: {method.name} due to it not returning anything"
                    )
                    continue

                return_type = self._is_list_return_type(method.returns, module)
                if not return_type:
                    logging.info(
                        f"Skipping bulk method creation for: {method.name} due to it not returning a list"
                    )
                    continue

                gql_var_name = f"{method.name.upper()}_GQL"
                bulk_method = self._create_bulk_method(
                    method, gql_var_name, module, return_type
                )
                if bulk_method:
                    new_methods.append(bulk_method)
                    logging.info(f"Bulk method created: {bulk_method.name}")
        return new_methods

    def _is_list_return_type(
        self, return_type: ast.expr, module: ast.Module
    ) -> Optional[ast.expr]:
        if isinstance(return_type, ast.Name):
            class_name = return_type.id
            result = self._get_class_ast(class_name, module)
            if result:
                class_ast, class_module_name = result
                class_def = self._find_class_in_ast(class_name, class_ast)
                if class_def:
                    for node in ast.walk(class_def):
                        if isinstance(node, ast.AnnAssign) and isinstance(
                            node.annotation, ast.Subscript
                        ) and isinstance(node.target, ast.Name) and node.target.id=="edges" and isinstance(node.annotation.value, ast.Name) and node.annotation.value.id == "List":
                            if isinstance(node.annotation.slice, ast.Constant) and node.annotation.slice.value:
                                node_class_def = self._find_class_in_ast(node.annotation.slice.value, class_ast)
                                if node_class_def:
                                    for class_node in ast.walk(node_class_def):
                                        if isinstance(class_node, ast.AnnAssign) and isinstance(class_node.target, ast.Name) and class_node.target.id=="node":
                                        #if isinstance(class_node, ast.AnnAssign) and isinstance(class_node.target, ast.Name) and class_node.target.id == 'node':
                                            # Add import to module body
                                            self._add_import_to_module(class_node.annotation, module, "."+class_module_name)
                                            return class_node.annotation
        return None

    def _get_class_ast(
        self, class_name: str, module: ast.Module
    ) -> Optional[Tuple[ast.Module, str]]:
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == class_name and node.module:
                        absolute_module = f"graphpyshop.client.{node.module}"
                        try:
                            # Get the file path of the module
                            module_file_path = absolute_module.replace(".", "/") + ".py"
                            with open(module_file_path, "r") as file:
                                class_source = file.read()
                            return ast.parse(class_source), node.module
                        except (FileNotFoundError, OSError) as e:
                            logging.warning(
                                f"Could not read {class_name} from {absolute_module}: {e}"
                            )
                            return None
        return None
    def _find_class_in_ast(
        self, class_name: str, class_ast: ast.Module
    ) -> Optional[ast.ClassDef]:
        for class_node in ast.walk(class_ast):
            if isinstance(class_node, ast.ClassDef) and class_node.name == class_name:
                return class_node
        return None
    
    def _add_import_to_module(self, class_name: ast.expr, module: ast.Module, module_name: str):
        if isinstance(class_name, ast.Subscript):
            # Handle subscript case (e.g., List[str])
            if isinstance(class_name.slice, ast.Constant):
                self._add_import(name=class_name.slice.value, module=module, module_name=module_name)
            elif isinstance(class_name.slice, ast.Tuple):
                for elt in class_name.slice.elts:
                    if isinstance(elt, ast.Constant):
                        self._add_import(name=elt.value, module=module, module_name=module_name)
        elif isinstance(class_name, ast.Constant):
            # Handle simple constant case (e.g., str)
            self._add_import(name=class_name.value, module=module, module_name=module_name)
        else:
            logging.error(f"Unsupported type for class_name: {type(class_name).__name__}")

    def _add_import(self, name: str, module: ast.Module, module_name: str):
        # Collect all names to be imported from the same module
        if not hasattr(self, 'pending_imports'):
            self.pending_imports = {}

        if module_name not in self.pending_imports:
            self.pending_imports[module_name] = set()

        self.pending_imports[module_name].add(name)

    def _flush_pending_imports(self, module: ast.Module):
        # Generate import statements for all collected names grouped by module
        for module_name, names in self.pending_imports.items():
            import_stmt = ast.ImportFrom(
                module=module_name,
                names=[ast.alias(name=name, asname=None) for name in names],
                level=0
            )
            module.body.insert(0, import_stmt)
            logging.info(f"Import for {', '.join(names)} from {module_name} added.")
            self.pending_imports = {}  # Reset after flushing


    def _create_bulk_method(
        self,
        method_def: ast.AsyncFunctionDef,
        gql_var_name: str,
        module: ast.Module,
        return_type: ast.expr,
    ) -> ast.AsyncFunctionDef:
        logging.info(f"Creating bulk method for: {method_def.name}")
        bulk_method_def = ast.AsyncFunctionDef(
            name=f"bq_{method_def.name}",
            args=method_def.args,  # Preserve original args
            body=self._generate_bulk_method_body(
                method_def, gql_var_name, module, return_type
            ),
            decorator_list=[],
            returns=ast.Subscript(
                value=ast.Name(id="AsyncGenerator", ctx=ast.Load()),
                slice=ast.Index(
                    value=ast.Tuple(
                        elts=[
                            return_type,
                            ast.Name(id="None", ctx=ast.Load()),
                        ],
                        ctx=ast.Load(),
                    )
                ),
                ctx=ast.Load(),
            ),
        )
        return bulk_method_def

    def _generate_bulk_method_body(
        self,
        method_def: ast.AsyncFunctionDef,
        gql_var_name: str,
        module: ast.Module,
        return_type: ast.expr,
    ):
        # Generate the variables assignment dynamically based on the method definition, excluding 'self'
        variables_assignment = ast.AnnAssign(
            target=ast.Name(id="variables", ctx=ast.Store()),
            annotation=ast.Subscript(
                value=ast.Name(id="Dict", ctx=ast.Load()),
                slice=ast.Tuple(
                    elts=[
                        ast.Name(id="str", ctx=ast.Load()),
                        ast.Name(id="Any", ctx=ast.Load()),
                    ],
                    ctx=ast.Load(),
                ),
                ctx=ast.Load(),
            ),
            value=ast.Dict(
                keys=[
                    ast.Constant(value=arg.arg)
                    for arg in method_def.args.args
                    if arg.arg not in self._ignore_args_full
                ],
                values=[
                    ast.Name(id=arg.arg, ctx=ast.Load())
                    for arg in method_def.args.args
                    if arg.arg not in self._ignore_args_full
                ],
            ),
            simple=1,
        )

        # Construct the call for the async for loop
        call_expression = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id="self", ctx=ast.Load()),
                attr="run_bulk_operation",
                ctx=ast.Load(),
            ),
            args=[
                ast.Attribute(
                    value=ast.Name(id="self", ctx=ast.Load()),
                    attr="bulk_operation_run_query",
                    ctx=ast.Load(),
                ),
                ast.Attribute(
                    value=ast.Name(id="self", ctx=ast.Load()),
                    attr="bulk_operation",
                    ctx=ast.Load(),
                ),
                ast.Name(id=gql_var_name, ctx=ast.Load()),
                ast.Name(id="variables", ctx=ast.Load()),
                ast.Name(id="BulkOperationStatus", ctx=ast.Load()),
                ast.Name(id="BulkOperationNodeBulkOperation", ctx=ast.Load()),
            ],
            keywords=[],
        )

        # Async for loop body with yield
        async_for_loop = ast.AsyncFor(
            target=ast.Name(id="item", ctx=ast.Store()),
            iter=call_expression,
            body=[ast.Expr(value=ast.Yield(value=ast.Name(id="item", ctx=ast.Load())))],
            orelse=[],
        )

        return [variables_assignment, async_for_loop]
