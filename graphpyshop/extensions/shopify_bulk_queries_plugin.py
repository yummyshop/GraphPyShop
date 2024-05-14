import ast
import logging
from typing import Dict

from graphql import GraphQLSchema
from ariadne_codegen.plugins.base import Plugin
from ariadne_codegen.codegen import (
    generate_import_from,
)

class ShopifyBulkQueriesPlugin(Plugin):
    _ignore_args = {}
    _ignore_args_full = {"self", *_ignore_args}

    def __init__(self, schema: GraphQLSchema, config_dict: Dict[str, any]) -> None:
        super().__init__(schema=schema, config_dict=config_dict)
        self.imported_types = {}  # Track imported types
        logging.info("ShopifyBulkQueriesPlugin initialized with schema and config.")

    def generate_client_module(self, module: ast.Module) -> ast.Module:
        logging.info("Starting to generate client module.")
        self._collect_imports(module)
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                logging.info(f"Examining class: {node.name}")
                new_methods = self._enhance_class_with_bulk_methods(node, module)
                node.body.extend(new_methods)  # Append new methods after collecting all

        ast.fix_missing_locations(module)  # Fix locations for the entire module
        self._add_necessary_imports(module)
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
        for method in class_def.body:
            if isinstance(method, ast.AsyncFunctionDef):
                logging.info(f"Found async function: {method.name}")
                # Skip if the method is already a bulk method
                if method.name.startswith("bq_"):
                    logging.info(
                        f"Skipping bulk method creation for already enhanced method: {method.name}"
                    )
                    continue
                # Check if the return type is Optional
                if self._is_optional_return_type(method.returns):
                    logging.info(
                        f"Skipping bulk method creation for: {method.name} due to Optional return type."
                    )
                    continue
                gql_var_name = f"{method.name.upper()}_GQL"
                bulk_method = self._create_bulk_method(method, gql_var_name, module)
                if bulk_method:
                    new_methods.append(bulk_method)
                    logging.info(f"Bulk method created: {bulk_method.name}")
        return new_methods

    def _is_optional_return_type(self, return_type):
        if isinstance(return_type, ast.Subscript) and isinstance(
            return_type.value, ast.Name
        ):
            return return_type.value.id == "Optional"
        return False

    def _create_bulk_method(
        self, method_def: ast.AsyncFunctionDef, gql_var_name: str, module: ast.Module
    ) -> ast.AsyncFunctionDef:
        logging.info(f"Creating bulk method for: {method_def.name}")
        bulk_method_def = ast.AsyncFunctionDef(
            name=f"bq_{method_def.name}",
            args=method_def.args,  # Preserve original args
            body=self._generate_bulk_method_body(method_def, gql_var_name, module),
            decorator_list=[],
            returns=self._process_return_type(
                method_def.returns, method_def
            ),  # Process while referencing the original method for positional data
        )
        return bulk_method_def

    def _generate_bulk_method_body(
        self, method_def: ast.AsyncFunctionDef, gql_var_name: str, module: ast.Module
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
                    ast.Str(s=arg.arg)
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

        # Determine the return type and corresponding edges node type
        return_type_id = self._get_return_type_id(method_def.returns)
        edges_node_type = f"{return_type_id}EdgesNode"

        # Use the same module path as the original return type for edges node type
        original_module_path = self.imported_types.get(
            return_type_id, f".{return_type_id.lower()}"
        )

        # Ensure the original return type and its edges node type are imported from the same module
        if return_type_id not in self.imported_types:
            module.body.insert(
                0, generate_import_from([return_type_id], original_module_path)
            )
            self.imported_types[return_type_id] = original_module_path

        if edges_node_type not in self.imported_types:
            edges_node_module_path = (
                original_module_path  # Same module path as the original return type
            )
            module.body.insert(
                0, generate_import_from([edges_node_type], edges_node_module_path)
            )
            self.imported_types[edges_node_type] = edges_node_module_path

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
                ast.Name(id=edges_node_type, ctx=ast.Load()),
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

    def _get_return_type_id(self, node):
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Subscript):
            return self._get_return_type_id(
                node.value
            )  # Recursive call to handle nested types
        return "Unknown"

    def _process_return_type(self, return_type, source_node):
        # Return type adjustment for AsyncGenerator
        return ast.Subscript(
            value=ast.Name(id="AsyncGenerator", ctx=ast.Load()),
            slice=ast.Index(
                value=ast.Tuple(
                    elts=[
                        ast.Name(
                            id=f"{self._get_return_type_id(return_type)}EdgesNode",
                            ctx=ast.Load(),
                        ),
                        ast.Name(id="None", ctx=ast.Load()),
                    ],
                    ctx=ast.Load(),
                )
            ),
            ctx=ast.Load(),
        )
