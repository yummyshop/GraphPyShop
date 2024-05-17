from typing import Any, Dict, Optional, List, Set, Union
from graphql import TypeDefinitionNode, build_ast_schema, print_schema, parse, print_ast, specified_rules, validate
from graphql.language.ast import (
    FieldDefinitionNode, TypeNode, NonNullTypeNode, ListTypeNode, NamedTypeNode, DocumentNode,
    ObjectTypeDefinitionNode, UnionTypeDefinitionNode, VariableDefinitionNode, VariableNode,
    NameNode, ArgumentNode, SelectionSetNode, FieldNode, InlineFragmentNode, InterfaceTypeDefinitionNode,
    ScalarTypeDefinitionNode, EnumTypeDefinitionNode, OperationDefinitionNode,
    OperationType, IntValueNode
)
import os
import logging
from ariadne_codegen.config import get_client_settings, get_config_dict
from ariadne_codegen.schema import get_graphql_schema_from_path, get_graphql_schema_from_url
from ariadne_codegen.settings import ClientSettings
from functools import lru_cache
import concurrent.futures
import threading
import time
class ShopifyQueryGenerator:
    def __init__(self, settings: Optional[ClientSettings] = None) -> None:
        self.max_depth_overrides: Dict[str, int] = {"checkoutBranding": 4}
        self.default_max_depth: int = 3
        self.core_types = {"String", "Int", "Float", "Boolean", "ID", "DateTime", "UnsignedInt64"}

        self.hardcoded_defaults: Dict[str, Any] = {"first": IntValueNode(value="250")}
        self.field_type_rules: Dict[str, Dict[str, List[str]]] = {
            "include": {"App": ["ID"], "CommentEventSubject": ["ID"]},
            "exclude": {"StoreCreditAccount": [], "Market": [], "MetafieldDefinitionConnection": [], "HasMetafields": [], "StaffMember": []}
        }
        self.field_name_rules: Dict[str, List[str]] = {
            "include": [],
            "exclude": ["legacyResourceId", "nodes", "metafield", "metafieldsByIdentifiers", "exchangeV2s", "originalSource", "hasCollection"]
        }

        if settings:
            self.set_schema(settings)

    def set_schema(self, settings: Optional[ClientSettings] = None, schema_override: Optional[str] = None):
        if not settings and not schema_override:
            raise ValueError("Either 'settings' or 'schema_override' must be provided.")
        self.settings: Optional[ClientSettings] = settings

        if schema_override:
            graphql_ast = parse(schema_override)
            self.schema = build_ast_schema(graphql_ast, assume_valid=True)
        elif self.settings.schema_path:
            logging.info(f"Loading schema from path: {self.settings.schema_path}")
            self.schema = get_graphql_schema_from_path(self.settings.schema_path)
        else:
            logging.info(f"Loading schema from URL: {self.settings.remote_schema_url}")
            self.schema = get_graphql_schema_from_url(
                url=self.settings.remote_schema_url,
                headers=self.settings.remote_schema_headers,
                verify_ssl=self.settings.remote_schema_verify_ssl,
            )
        self.sdl = print_schema(self.schema)
        if not schema_override and not self.settings.schema_path:
            with open(f"{self.settings.target_package_path}/schema.graphql", 'w') as schema_file:
                schema_file.write(self.sdl)
            logging.info(f"Schema written to {self.settings.target_package_path}/schema.graphql")
        self.ast = parse(self.sdl)

        self.list_returning_queries: Dict[str, str] = self.extract_list_returning_queries()
        self.list_returning_queries_by_type: Dict[str, List[str]] = self.reverse_list_returning_queries()
        self.direct_object_references: Dict[str, List[str]] = self.extract_direct_object_references()
        self.scalar_types: Set[str] = {definition.name.value for definition in self.ast.definitions if isinstance(definition, ScalarTypeDefinitionNode)}
        self.enum_types: Set[str] = {definition.name.value for definition in self.ast.definitions if isinstance(definition, EnumTypeDefinitionNode)}
        self.type_definition_map: Dict[str, TypeDefinitionNode] = self.create_type_definition_map()
        self.used_variables: Dict[str, Dict[str, VariableDefinitionNode]] = {}


    def is_deprecated(self, field: FieldDefinitionNode) -> bool:
        return any(directive.name.value == 'deprecated' for directive in field.directives)

    def get_field_type(self, field_type: TypeNode) -> TypeNode:
        while isinstance(field_type, (NonNullTypeNode, ListTypeNode)):
            field_type = field_type.type
        return field_type

    @lru_cache(maxsize=None)
    def get_field_type_name(self, field_type: TypeNode) -> str:
        while isinstance(field_type, (NonNullTypeNode, ListTypeNode)):
            field_type = field_type.type
        return field_type.name.value if isinstance(field_type, NamedTypeNode) else ""

    @lru_cache(maxsize=None)
    def get_ultimate_object(self, type_node: TypeNode) -> str:
        while isinstance(type_node, (NonNullTypeNode, ListTypeNode)):
            type_node = type_node.type
        if isinstance(type_node, NamedTypeNode):
            return type_node.name.value
        return ""

    def find_ultimate_object(self, type_name: str) -> str:
        for definition in self.ast.definitions:
            if isinstance(definition, (ObjectTypeDefinitionNode, UnionTypeDefinitionNode)) and definition.name.value == type_name:
                if isinstance(definition, ObjectTypeDefinitionNode):
                    for field in definition.fields:
                        field_type = self.get_field_type(field.type)
                        if isinstance(field_type, ObjectTypeDefinitionNode):
                            for sub_field in field_type.fields:
                                if sub_field.name.value == "node":
                                    return self.get_ultimate_object(sub_field.type)
                        elif field.name.value == "nodes":
                            return self.get_ultimate_object(field.type)
                else:
                    for type_ in definition.types:
                        return type_.name.value
        return type_name

    def returns_a_list(self, field: FieldDefinitionNode) -> bool:
        field_type_name = self.get_field_type_name(field.type)
        return field_type_name.endswith("Connection") or isinstance(field.type, ListTypeNode)

    def handle_arguments(self, field: FieldDefinitionNode, variables: Dict[str, VariableDefinitionNode], field_type_name: str, query_name: str) -> List[ArgumentNode]:
        arguments: List[ArgumentNode] = []
        for arg in field.arguments:
            type_name = self.get_field_type_name(arg.type)
            variable_name = f"{field.name.value}_{arg.name.value}"
            if type_name not in self.core_types:
                variable_name += f"_{type_name}"
            if variable_name not in variables:
                default_value = self.hardcoded_defaults.get(arg.name.value, arg.default_value)
                variables[variable_name] = VariableDefinitionNode(
                    variable=VariableNode(name=NameNode(value=variable_name)),
                    type=arg.type,
                    default_value=default_value
                )
            arguments.append(ArgumentNode(
                name=NameNode(value=arg.name.value),
                value=VariableNode(name=NameNode(value=variable_name))
            ))
            self.used_variables[query_name][variable_name] = variables[variable_name]
        return arguments

    def create_type_definition_map(self) -> Dict[str, TypeDefinitionNode]:
        type_definition_map: Dict[str, TypeDefinitionNode] = {}
        for definition in self.ast.definitions:
            if isinstance(definition, (ObjectTypeDefinitionNode, InterfaceTypeDefinitionNode, UnionTypeDefinitionNode)):
                type_definition_map[definition.name.value] = definition
        return type_definition_map

    def generate_subfield_selections(self, field_type_name: str, query_return_type: str | None, query_name: str, definition: ObjectTypeDefinitionNode | InterfaceTypeDefinitionNode | UnionTypeDefinitionNode, depth: int, max_depth: int, field: FieldDefinitionNode, current_path: str, variables: Dict[str, VariableDefinitionNode]) -> List[FieldNode]:
        selections: List[FieldNode] = []
        
        if isinstance(definition, (ObjectTypeDefinitionNode, InterfaceTypeDefinitionNode)):
            sub_fields: List[FieldDefinitionNode] = list(definition.fields)
        else:
            sub_fields: List[FieldDefinitionNode] = []
            for type_ in definition.types:
                type_name = type_.name.value
                if type_name in self.type_definition_map:
                    sub_definition = self.type_definition_map[type_name]
                    if isinstance(sub_definition, (ObjectTypeDefinitionNode, InterfaceTypeDefinitionNode)):
                        sub_fields.extend(sub_definition.fields)
        
        for sub_field in sub_fields:
            new_depth = depth if sub_field.name.value in {"edges", "node", "pageInfo"} else depth + 1
            sub_query = self.generate_query_ast(query_name, sub_field, new_depth, max_depth, field, current_path, variables)
            if isinstance(sub_query, FieldNode) or (isinstance(sub_query, SelectionSetNode) and sub_query.selections):
                sub_arguments = self.handle_arguments(sub_field, variables, field_type_name, query_name)
                if isinstance(sub_query, SelectionSetNode):
                    sub_query = FieldNode(
                        name=NameNode(value=sub_field.name.value),
                        selection_set=sub_query,
                        arguments=sub_arguments
                    )
                selections.append(sub_query)
        return selections

    def generate_query_ast(self, query_name: str, field: FieldDefinitionNode, depth: int, max_depth: int, parent: Optional[FieldDefinitionNode] = None, path: str = "", variables: Dict[str, VariableDefinitionNode] = {}) -> SelectionSetNode | FieldNode:
        current_path = f"{path} > {field.name.value}" if path else field.name.value
        parent_type_name = self.get_field_type_name(parent.type) if parent else None
        field_type_name = self.get_field_type_name(field.type)
        ultimate_field_type_name = self.find_ultimate_object(field_type_name)
        query_return_type = self.list_returning_queries.get(query_name, None)

        if field.name.value in self.field_name_rules["exclude"]:
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping field {field.name.value} as it is in the exclude list")
            return SelectionSetNode(selections=[])
        
        if ultimate_field_type_name in self.field_type_rules["exclude"].keys():
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping as it's an excluded field")
            return SelectionSetNode(selections=[])
        
        if depth > max_depth:
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached. Returning empty selection set.")
            return SelectionSetNode(selections=[])
        
        if self.is_deprecated(field):
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {field.name.value} is deprecated. Skipping.")
            return SelectionSetNode(selections=[])
        
        if depth != 0 and any(isinstance(arg.type, NonNullTypeNode) for arg in field.arguments):
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping field {field.name.value} as it has required non-null arguments")
            return SelectionSetNode(selections=[])
        
        if ultimate_field_type_name in self.list_returning_queries_by_type:
            if ultimate_field_type_name in self.direct_object_references and query_return_type in self.direct_object_references[ultimate_field_type_name]:
                logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping field as it matches direct object reference.")
        
        if parent_type_name and parent_type_name != query_return_type and parent_type_name in self.field_type_rules["include"].keys() and field_type_name not in self.field_type_rules["include"][parent_type_name]:
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field type {parent_type_name} includes subfield type {field_type_name}, returning empty set")
            return SelectionSetNode(selections=[])
        
        if parent_type_name != "Metafield" and parent_type_name in self.list_returning_queries_by_type and query_return_type!=parent_type_name and field_type_name != "ID":
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] It's a list returning field and type is not id, returning empty set")
            return SelectionSetNode(selections=[])

        selections: List[Union[FieldNode, InlineFragmentNode]] = []
        if self.is_core_type(field_type_name):
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Adding core type field {field.name.value}")
            sub_arguments = self.handle_arguments(field, variables, field_type_name, query_name)
            selections.append(FieldNode(
                name=NameNode(value=field.name.value),
                arguments=sub_arguments,
            ))
        else:
            if field_type_name in self.type_definition_map:
                definition = self.type_definition_map[field_type_name]
                if isinstance(definition, ObjectTypeDefinitionNode):
                    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Processing ObjectTypeDefinitionNode: {definition.name.value}")
                    subfield_selections = self.generate_subfield_selections(field_type_name, query_return_type, query_name, definition, depth, max_depth, field, current_path, variables)
                    if subfield_selections:
                        sub_arguments = self.handle_arguments(field, variables, field_type_name, query_name)
                        selections.append(FieldNode(
                            name=NameNode(value=field.name.value),
                            selection_set=SelectionSetNode(selections=subfield_selections),
                            arguments=sub_arguments
                        ))
                        subfield_selections.append(FieldNode(
                            name=NameNode(value="__typename")
                        ))
                if isinstance(definition, (InterfaceTypeDefinitionNode, UnionTypeDefinitionNode)) and definition.name.value == field_type_name:
                    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Processing {type(definition).__name__}: {definition.name.value}")
                    interface_fields = self.generate_subfield_selections(field_type_name, query_return_type, query_name, definition, depth, max_depth, field, current_path, variables)
                    selections.extend(interface_fields)
                    for object_definition in self.ast.definitions:
                        if isinstance(object_definition, ObjectTypeDefinitionNode) and (
                            definition.name.value in [interface.name.value for interface in object_definition.interfaces] or
                            definition.name.value in [union_type.name.value for union_type in getattr(object_definition, 'types', [])]
                        ):
                            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Found implementing type: {object_definition.name.value}")
                            fragment_selections = self.generate_subfield_selections(field_type_name, query_return_type, query_name, object_definition, depth, max_depth, field, current_path, variables)
                            if fragment_selections:
                                fragment_selections.append(FieldNode(
                                    name=NameNode(value="__typename")
                                ))
                                selections.append(InlineFragmentNode(
                                    type_condition=NamedTypeNode(name=NameNode(value=object_definition.name.value)),
                                    selection_set=SelectionSetNode(selections=fragment_selections)
                                ))
        
        if len(selections) == 0:
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {field.name.value} has no children. Skipping nested selection.")
            return SelectionSetNode(selections=[])
        
        if any(isinstance(selection, FieldNode) and selection.name.value == "edges" for selection in selections):
            selections = [selection for selection in selections if not (isinstance(selection, FieldNode) and selection.name.value in {"nodes"})]
            
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Returning selection set with {len(selections)} selections.")

        if len(selections) == 1 and isinstance(selections[0], FieldNode):
            return selections[0]
        
        return SelectionSetNode(selections=selections)
    
    def generate_query_with_variables_ast(self, query_name: str, field: FieldDefinitionNode, depth: int, max_depth: int) -> DocumentNode:
        self.used_variables[query_name] = {}

        variables: Dict[str, VariableDefinitionNode] = {}
        query_fields = self.generate_query_ast(query_name, field, depth, max_depth, variables=variables)

        return DocumentNode(
            definitions=[   
                OperationDefinitionNode(
                    operation=OperationType.QUERY,
                    name=NameNode(value=field.name.value),
                    variable_definitions=list(self.used_variables[query_name].values()),
                    selection_set=SelectionSetNode(selections=[query_fields] if isinstance(query_fields, FieldNode) else query_fields.selections)
                )
            ]
        )

    def is_core_type(self, type_name: str) -> bool:
        return type_name in self.core_types or type_name in self.scalar_types or type_name in self.enum_types

    def extract_list_returning_queries(self) -> Dict[str, str]:
        list_returning_queries: Dict[str, str] = {}
        for definition in self.ast.definitions:
            if isinstance(definition, ObjectTypeDefinitionNode) and definition.name.value == "QueryRoot":
                for field in definition.fields:
                    if not self.is_deprecated(field):
                        field_type_name = self.get_field_type_name(field.type)
                        ultimate_object = self.find_ultimate_object(field_type_name)
                        if self.returns_a_list(field):
                            list_returning_queries[field.name.value] = ultimate_object
        return list_returning_queries

    def reverse_list_returning_queries(self) -> Dict[str, List[str]]:
        list_returning_queries_by_type: Dict[str, List[str]] = {}
        for key, value in self.list_returning_queries.items():
            if value in list_returning_queries_by_type:
                list_returning_queries_by_type[value].append(key)
            else:
                list_returning_queries_by_type[value] = [key]
        return list_returning_queries_by_type

    def extract_direct_object_references(self) -> Dict[str, List[str]]:
        direct_object_references: Dict[str, List[str]] = {}
        for key in self.list_returning_queries_by_type:
            direct_references: Set[str] = set()
            for definition in self.ast.definitions:
                if isinstance(definition, ObjectTypeDefinitionNode) and definition.name.value == key:
                    for field in definition.fields:
                        field_type = self.get_field_type(field.type)
                        if isinstance(field_type, NamedTypeNode) and field_type.name.value in self.list_returning_queries_by_type:
                            direct_references.add(field_type.name.value)
            if key == "MetafieldDefinition":
                for enum_definition in self.ast.definitions:
                    if isinstance(enum_definition, EnumTypeDefinitionNode) and enum_definition.name.value == "MetafieldOwnerType":
                        for enum_value in enum_definition.values:
                            formatted_value = ''.join(word.capitalize() for word in enum_value.name.value.split('_'))
                            direct_references.add(formatted_value)
            if direct_references:
                direct_object_references[key] = list(direct_references)
        return direct_object_references

    def process_field(self, field: FieldDefinitionNode, included_queries: List[str], excluded_queries: List[str], write_invalid: bool) -> Optional[str]:
        start_time = time.time()
        
        query_name = field.name.value
        ultimate_object = self.find_ultimate_object(self.get_field_type_name(field.type))
        if ultimate_object in self.field_type_rules["exclude"]:
            return None
        if (not included_queries or query_name in included_queries) and (not excluded_queries or query_name not in excluded_queries):
            max_depth = self.max_depth_overrides.get(query_name, self.default_max_depth)
            query_ast = self.generate_query_with_variables_ast(query_name, field, 0, max_depth)
            query_str = print_ast(query_ast)
            
            try:
                validation_errors = validate(
                    schema=self.schema,
                    document_ast=parse(query_str),
                    rules=specified_rules,
                )

                if validation_errors:
                    for error in validation_errors:
                        logging.error(f"Validation error in query {query_name}: {error.message}")
                    if not write_invalid:
                        return None
                else:
                    logging.info(f"All validations passed for query {query_name}")
                
            except Exception as e:
                logging.error(f"An error occurred during validation for query {query_name}: {e}")
                if not write_invalid:
                    return None
                
            end_time = time.time()
            elapsed_time = end_time - start_time
            logging.info(f"Generated query for {query_name}, {elapsed_time:.2f} seconds")
            return query_str
        return None

    def write_query_to_file(self, query_name: str, query_str: str) -> None:
        if not hasattr(self, '_dirs_checked'):
            os.makedirs(self.settings.queries_path, exist_ok=True)
            os.makedirs(f"{self.settings.queries_path}/lists", exist_ok=True)
            os.makedirs(f"{self.settings.queries_path}/objects", exist_ok=True)
            self._dirs_checked = True
        
        if query_name in self.list_returning_queries:
            output_dir = f"{self.settings.queries_path}/lists"
        else:
            output_dir = f"{self.settings.queries_path}/objects"
        
        output_file = f"{output_dir}/{query_name}.graphql"
        try:
            with open(output_file, "w") as f:
                f.write(query_str)
        except Exception as e:
            logging.error(f"Failed to write query for {query_name} to {output_file}: {e}")


    def generate_queries(self, include_definitions: List[str] = ['QueryRoot'], included_queries: List[str] = [], excluded_queries: List[str] = ["node", "nodes", "metafields", "job"], write_invalid: bool = False, concurrent: bool = False, return_queries: bool = False) -> Union[None, List[str]]:
        start_time = time.time()
        logging.info("Starting generation of queries")

        queries = []
        if concurrent:
            num_threads = threading.active_count()
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = []
                for definition in self.ast.definitions:
                    if isinstance(definition, ObjectTypeDefinitionNode):
                        type_name = definition.name.value
                        if type_name not in include_definitions:
                            continue
                        for field in definition.fields:
                            if not self.is_deprecated(field):
                                futures.append(executor.submit(self.process_field, field, included_queries, excluded_queries, write_invalid))

                concurrent.futures.wait(futures)
        else:
            futures = []
            for definition in self.ast.definitions:
                if isinstance(definition, ObjectTypeDefinitionNode):
                    type_name = definition.name.value
                    if type_name not in include_definitions:
                        continue
                    for field in definition.fields:
                        if not self.is_deprecated(field):
                            query_str = self.process_field(field, included_queries, excluded_queries, write_invalid)
                            if query_str:
                                if return_queries:
                                    queries.append(query_str)
                                else:
                                    self.write_query_to_file(field.name.value, query_str)
                            futures.append(None)  # Just to keep count of the number of queries processed
        
        end_time = time.time()
        total_time = end_time - start_time
        average_time_per_query = total_time / len(futures) if futures else 0
        num_queries_generated = len(futures)
        logging.info(f"Total time taken for generating queries: {total_time:.2f} seconds, Average time per query: {average_time_per_query:.2f} seconds, Number of queries generated: {num_queries_generated}")

        if return_queries:
            return queries
        return None
                            
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    config_dict = get_config_dict()
    settings = get_client_settings(config_dict)

    schema_path = f"{settings.target_package_path}/schema.graphql"
    
    logging.info(f"Looking for schema at {schema_path}")
    
    if os.path.exists(schema_path):
        logging.info(f"Schema found at {schema_path}")
        settings.schema_path = schema_path
    else:
        logging.info("Schema not found, will write schema to file")
        #settings.write_schema_to_file = True
    
    query_generator = ShopifyQueryGenerator(settings)
    query_generator.generate_queries(included_queries=["catalog","catalogs"],write_invalid=True)
