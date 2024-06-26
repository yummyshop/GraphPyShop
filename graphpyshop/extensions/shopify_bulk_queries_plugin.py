import ast
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from ariadne_codegen.plugins.base import Plugin
from graphql import GraphQLSchema


class ShopifyBulkQueriesPlugin(Plugin):
    _ignore_args: set[str] = set()
    _ignore_args_full = {"self", *_ignore_args}
    _typename_to_class_map: Dict[str, Dict[str, str]] = {}

    def __init__(self, schema: GraphQLSchema, config_dict: Dict[str, Any]) -> None:
        super().__init__(schema=schema, config_dict=config_dict)
        self.pending_imports: Dict[str, Set[Tuple[str, bool]]] = (
            {}
        )  # Initialize pending imports
        logging.info("ShopifyBulkQueriesPlugin initialized with schema and config.")

    def get_typename_to_class_map(self, module_name: str) -> Dict[str, str]:
        if module_name in self._typename_to_class_map:
            return self._typename_to_class_map[module_name]

        module_path = f"graphpyshop/client/{module_name.replace('.', '/')}.py"
        try:
            with open(module_path, "r") as file:
                module_source = file.read()
            module_ast = ast.parse(module_source)
        except (FileNotFoundError, OSError) as e:
            logging.error(f"Failed to read module {module_name}: {e}")
            return {}

        typename_to_class_map: Dict[str, str] = {}
        for node in ast.walk(module_ast):
            if isinstance(node, ast.ClassDef):
                typename_literal = self._extract_typename_literal(node)
                if typename_literal:
                    typename_to_class_map[typename_literal] = node.name

        self._typename_to_class_map[module_name] = typename_to_class_map
        return typename_to_class_map

    def _extract_typename_literal(self, class_node: ast.ClassDef) -> Optional[str]:
        for node in ast.walk(class_node):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "typename__"
            ):
                if (
                    isinstance(node.annotation, ast.Subscript)
                    and isinstance(node.annotation.value, ast.Name)
                    and node.annotation.value.id == "Literal"
                ):
                    if isinstance(node.annotation.slice, ast.Constant):
                        return node.annotation.slice.value
        return None

    def generate_client_module(self, module: ast.Module) -> ast.Module:
        logging.info("Starting to generate client module.")
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                logging.info(f"Examining class: {node.name}")
                new_methods = self._enhance_class_with_bulk_methods(node, module)
                node.body.extend(new_methods)  # Append new methods after collecting all

        self._add_necessary_imports(module)
        self._flush_pending_imports(module)  # Ensure all pending imports are added
        ast.fix_missing_locations(module)
        return module

    def _add_necessary_imports(self, module: ast.Module):
        # Ensure AsyncGenerator from typing is always imported
        if not any(
            isinstance(stmt, ast.ImportFrom)
            and stmt.module == "typing"
            and any(
                isinstance(alias, ast.alias) and alias.name == "AsyncGenerator"
                for alias in stmt.names
            )
            for stmt in module.body
        ):
            self._add_import("AsyncGenerator", "typing", False)

        # Ensure BulkOperationStatus from .enums is always imported
        if not any(
            isinstance(stmt, ast.ImportFrom)
            and stmt.module == ".enums"
            and any(
                isinstance(alias, ast.alias) and alias.name == "BulkOperationStatus"
                for alias in stmt.names
            )
            for stmt in module.body
        ):
            self._add_import("BulkOperationStatus", ".enums", False)
            self._add_import("BulkOperationNodeBulkOperation", ".bulk_operation", False)

    def _enhance_class_with_bulk_methods(
        self, class_def: ast.ClassDef, module: ast.Module
    ) -> List[ast.AsyncFunctionDef]:
        new_methods: List[ast.AsyncFunctionDef] = []
        existing_method_names: Set[str] = {
            method.name
            for method in class_def.body
            if isinstance(method, ast.AsyncFunctionDef)
        }

        for method in class_def.body:
            if isinstance(method, ast.AsyncFunctionDef):

                bulk_method_name: str = f"bq_{method.name}"

                if bulk_method_name in existing_method_names:
                    logging.info(
                        f"Skipping bulk method creation for already existing method: {bulk_method_name}"
                    )
                    continue

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

                ret: Optional[Tuple[ast.expr, str]] = self._is_list_return_type(
                    method.returns, module
                )

                if ret:
                    return_type, module_name = ret

                    gql_var_name: str = f"{method.name.upper()}_GQL"
                    bulk_method: Optional[ast.AsyncFunctionDef] = (
                        self._create_bulk_method(
                            method, gql_var_name, module_name, return_type
                        )
                    )
                    if bulk_method:
                        new_methods.append(bulk_method)
                        existing_method_names.add(bulk_method_name)
                        logging.info(f"Bulk method created: {bulk_method.name}")
                else:
                    logging.info(
                        f"Skipping bulk method creation for: {method.name} due to it not returning a list"
                    )

        return new_methods

    def _is_list_return_type(
        self, return_type: ast.expr, module: ast.Module
    ) -> Optional[Tuple[ast.expr, str]]:
        if isinstance(return_type, ast.Constant) or isinstance(return_type, ast.Name):
            if isinstance(return_type, ast.Constant):
                class_name = return_type.value
            else:
                class_name = return_type.id
            result = self._get_class_ast(class_name, module)
            if result:
                class_ast, class_module_name = result
                if isinstance(return_type, ast.Name):
                    class_module_name = "." + class_module_name
                class_def = self._find_class_in_ast(class_name, class_ast)
                if class_def:
                    for node in ast.walk(class_def):
                        if (
                            isinstance(node, ast.AnnAssign)
                            and isinstance(node.annotation, ast.Subscript)
                            and isinstance(node.target, ast.Name)
                            and node.target.id == "edges"
                            and isinstance(node.annotation.value, ast.Name)
                            and node.annotation.value.id == "List"
                        ):
                            if (
                                isinstance(node.annotation.slice, ast.Constant)
                                and node.annotation.slice.value
                            ):
                                node_class_def = self._find_class_in_ast(
                                    node.annotation.slice.value, class_ast
                                )
                                if node_class_def:
                                    for class_node in ast.walk(node_class_def):
                                        if (
                                            isinstance(class_node, ast.AnnAssign)
                                            and isinstance(class_node.target, ast.Name)
                                            and class_node.target.id == "node"
                                        ):
                                            self._add_import_to_module(
                                                class_node.annotation,
                                                class_module_name,
                                            )
                                            return (
                                                class_node.annotation,
                                                class_module_name,
                                            )
        return None

    def _get_class_ast(
        self, class_name: str, module: ast.Module
    ) -> Optional[Tuple[ast.Module, str]]:
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (
                        isinstance(alias, ast.alias)
                        and alias.name == class_name
                        and node.module
                    ):
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

    def _add_import_to_module(self, class_name: ast.expr, module_name: str):
        if isinstance(class_name, ast.Subscript):
            # Handle subscript case (e.g., List[str])
            if isinstance(class_name.slice, ast.Constant):
                self._add_import(name=class_name.slice.value, module_name=module_name)
            elif isinstance(class_name.slice, ast.Tuple):
                for elt in class_name.slice.elts:
                    if isinstance(elt, ast.Constant):
                        self._add_import(name=elt.value, module_name=module_name)
        elif isinstance(class_name, ast.Constant):
            # Handle simple constant case (e.g., str)
            self._add_import(name=class_name.value, module_name=module_name)
        else:
            logging.error(
                f"Unsupported type for class_name: {type(class_name).__name__}"
            )

    def _add_import(
        self, name: str, module_name: str, under_type_checking: bool = True
    ) -> None:
        if module_name not in self.pending_imports:
            self.pending_imports[module_name] = set()

        self.pending_imports[module_name].add((name, under_type_checking))

    def _flush_pending_imports(self, module: ast.Module) -> None:
        # Generate import statements for all collected names grouped by module
        type_checking_node = None
        for node in module.body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                type_checking_node = node
                break

        for module_name, names in self.pending_imports.items():
            for name, under_type_checking in names:
                import_stmt = ast.ImportFrom(
                    module=module_name,
                    names=[ast.alias(name=name, asname=None)],
                    level=0,
                )
                if type_checking_node and under_type_checking:
                    type_checking_node.body.append(import_stmt)
                    logging.info(
                        f"Import for {name} from {module_name} added under TYPE_CHECKING."
                    )
                else:
                    module.body.insert(0, import_stmt)
                    logging.info(
                        f"Import for {name} from {module_name} added outside TYPE_CHECKING."
                    )

        self.pending_imports = {}  # Reset after flushing

    def _create_bulk_method(
        self,
        method_def: ast.AsyncFunctionDef,
        gql_var_name: str,
        module_name: str,
        return_type: ast.expr,
    ) -> ast.AsyncFunctionDef:
        logging.info(f"Creating bulk method for: {method_def.name}")
        bulk_method_def = ast.AsyncFunctionDef(
            name=f"bq_{method_def.name}",
            args=method_def.args,  # Preserve original args
            body=self._generate_bulk_method_body(method_def, gql_var_name, module_name),
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

    def _create_import_statement(self, class_name: str, module_name: str):
        return ast.ImportFrom(
            module=module_name, names=[ast.alias(name=class_name, asname=None)], level=0
        )

    def _generate_bulk_method_body(
        self, method_def: ast.AsyncFunctionDef, gql_var_name: str, module_name: str
    ):
        # Create import statements
        import_stmt_bulk_operation = [] 
        """
        import_stmt_bulk_operation = ast.ImportFrom(
            module=".bulk_operation",
            names=[
                ast.alias(name="BulkOperationNodeBulkOperation", asname=None),
            ],
            level=0,
        )
        """

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
                ast.Dict(
                    keys=[
                        ast.Constant(value=key)
                        for key in self.get_typename_to_class_map(module_name).keys()
                    ],
                    values=[
                        ast.Constant(value=value)
                        for value in self.get_typename_to_class_map(
                            module_name
                        ).values()
                    ],
                ),
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

        return [import_stmt_bulk_operation, variables_assignment, async_for_loop]
