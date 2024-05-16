from typing import Any, Dict, Optional, List, Union
from graphql import TypeDefinitionNode, print_schema, parse, print_ast, specified_rules, validate
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

class ShopifyQueryGenerator:
    def __init__(self, settings: ClientSettings):
        self.settings: ClientSettings = settings
        self.max_depth_overrides: Dict[str, int] = {"checkoutBranding": 4}
        self.default_max_depth: int = 2
        self.hardcoded_defaults: Dict[str, Any] = {"first": IntValueNode(value="250")}
        self.field_type_rules: Dict[str, Dict[str, List[str]]] = {
            "include": {"App": ["ID"], "CommentEventSubject": ["ID"]},
            "exclude": {"StoreCreditAccount": [], "Market": [], "MetafieldDefinitionConnection": [], "HasMetafields": [], "StaffMember": []}
        }
        self.field_name_rules: Dict[str, List[str]] = {
            "include": [],
            "exclude": ["legacyResourceId", "nodes", "metafield", "metafieldsByIdentifiers", "exchangeV2s", "originalSource", "hasCollection"]
        }

       
        if self.settings.schema_path:
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
        if not self.settings.schema_path:
            with open(f"{self.settings.target_package_path}/schema.graphql", 'w') as schema_file:
                schema_file.write(self.sdl)
            logging.info(f"Schema written to {self.settings.target_package_path}/schema.graphql")
        self.ast = parse(self.sdl)

        

        self.list_returning_queries = self.extract_list_returning_queries()
        self.list_returning_queries_by_type = self.reverse_list_returning_queries()
        self.direct_object_references = self.extract_direct_object_references()
        self.scalar_types = {definition.name.value for definition in self.ast.definitions if isinstance(definition, ScalarTypeDefinitionNode)}
        self.enum_types = {definition.name.value for definition in self.ast.definitions if isinstance(definition, EnumTypeDefinitionNode)}
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
                elif isinstance(definition, UnionTypeDefinitionNode):
                    for type_ in definition.types:
                        return type_.name.value
        return type_name

    def returns_a_list(self, field: FieldDefinitionNode) -> bool:
        field_type_name = self.get_field_type_name(field.type)
        return field_type_name.endswith("Connection") or isinstance(field.type, ListTypeNode)

    def handle_arguments(self, field: FieldDefinitionNode, variables: Dict[str, VariableDefinitionNode], field_type_name: str, query_name: str) -> List[ArgumentNode]:
        arguments = []
        for arg in field.arguments:
            variable_name = f"{field.name.value}_{arg.name.value}_{field_type_name}"
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
        for sub_field in definition.fields:
            new_depth = depth if sub_field.name.value in {"edges", "node", "pageInfo"} else depth + 1
            sub_query = self.generate_query_ast(query_name, sub_field, new_depth, max_depth, field, current_path, variables)
            if len(sub_query.selections) == 0 and not self.is_core_type(self.get_field_type_name(sub_field.type)):
                logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {sub_field.name.value} should have children but doesn't. Returning empty selection set.")
                continue
            sub_arguments = self.handle_arguments(sub_field, variables, field_type_name, query_name)
            selections.append(FieldNode(
                name=NameNode(value=sub_field.name.value),
                arguments=sub_arguments,
                selection_set=sub_query
            ))
        return selections

    def generate_query_ast(self, query_name: str, field: FieldDefinitionNode, depth: int, max_depth: int, parent: Optional[FieldDefinitionNode] = None, path: str = "", variables: Dict[str, VariableDefinitionNode] = {}) -> SelectionSetNode:
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
                
        if parent_type_name != query_return_type and parent_type_name in self.field_type_rules["include"] and field_type_name not in self.field_type_rules["include"][parent_type_name]:
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field type {parent_type_name} includes subfield type {field_type_name}, returning empty set")
            return SelectionSetNode(selections=[])
        
        
        if parent_type_name != "Metafield" and parent_type_name in self.list_returning_queries_by_type and query_return_type!=parent_type_name and field_type_name != "ID":
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] It's a list returning field and type is not id, returning empty set")
            return SelectionSetNode(selections=[])

        selections: List[Union[FieldNode, InlineFragmentNode]] = []
        if field_type_name in self.type_definition_map:
            definition = self.type_definition_map[field_type_name]
            if isinstance(definition, ObjectTypeDefinitionNode):
                logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Processing ObjectTypeDefinitionNode: {definition.name.value}")
                selections.extend(self.generate_subfield_selections(field_type_name, query_return_type, query_name, definition, depth, max_depth, field, current_path, variables))
                if selections:
                    selections.append(FieldNode(
                        name=NameNode(value="__typename")
                    ))
            if isinstance(definition, (InterfaceTypeDefinitionNode, UnionTypeDefinitionNode)) and definition.name.value == field_type_name:
                logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Processing {type(definition).__name__}: {definition.name.value}")
                for object_definition in self.ast.definitions:
                    if isinstance(object_definition, ObjectTypeDefinitionNode) and (
                        definition.name.value in [interface.name.value for interface in object_definition.interfaces] or
                        definition.name.value in [union_type.name.value for union_type in getattr(object_definition, 'types', [])]
                    ):
                        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Found implementing type: {object_definition.name.value}")
                        fragment_selections = self.generate_subfield_selections(field_type_name, query_return_type, query_name, definition, depth, max_depth, field, current_path, variables)
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
        return SelectionSetNode(selections=selections)

    def generate_query_with_variables_ast(self, query_name: str, field: FieldDefinitionNode, depth: int, max_depth: int) -> DocumentNode:
        self.used_variables[query_name] = {}

        variables: Dict[str, VariableDefinitionNode] = {}
        query_fields = self.generate_query_ast(query_name, field, depth, max_depth, variables=variables)
        arguments: List[ArgumentNode] = []
        if field.arguments:
            arguments = self.handle_arguments(field, variables, self.get_field_type_name(field.type), query_name)
        root_field = FieldNode(
            name=NameNode(value=field.name.value),
            arguments=arguments if arguments else [],
            selection_set=query_fields
        )

        return DocumentNode(
            definitions=[   
                OperationDefinitionNode(
                    operation=OperationType.QUERY,
                    name=NameNode(value=field.name.value),
                    variable_definitions=list(self.used_variables[query_name].values()),
                    selection_set=SelectionSetNode(selections=[root_field])
                )
            ]
        )

    def is_core_type(self, type_name: str) -> bool:
        core_types = {"String", "Int", "Float", "Boolean", "ID", "DateTime", "UnsignedInt64"}
        return type_name in core_types or type_name in self.scalar_types or type_name in self.enum_types

    def extract_list_returning_queries(self) -> Dict[str, str]:
        list_returning_queries = {}
        for definition in self.ast.definitions:
            if isinstance(definition, ObjectTypeDefinitionNode) and definition.name.value == "QueryRoot":
                for field in definition.fields:
                    if not self.is_deprecated(field):
                        field_type_name = self.get_field_type_name(field.type)
                        ultimate_object = self.find_ultimate_object(field_type_name)
                        if self.returns_a_list(field):
                            list_returning_queries[field.name.value] = ultimate_object
        return list_returning_queries

    def reverse_list_returning_queries(self) -> Dict[str, Union[str, List[str]]]:
        list_returning_queries_by_type = {}
        for key, value in self.list_returning_queries.items():
            if value in list_returning_queries_by_type:
                if isinstance(list_returning_queries_by_type[value], list):
                    list_returning_queries_by_type[value].append(key)
                else:
                    list_returning_queries_by_type[value] = [list_returning_queries_by_type[value], key]
            else:
                list_returning_queries_by_type[value] = key
        return list_returning_queries_by_type

    def extract_direct_object_references(self) -> Dict[str, List[str]]:
        direct_object_references = {}
        for key in self.list_returning_queries_by_type:
            direct_references = set()
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

    def shopify_generate_queries(self, include_definitions: List[str] = ['QueryRoot'], exclude_definitions: List[str] = [], included_queries: List[str] = [], excluded_queries: List[str] = ["node", "nodes", "metafields", "job"], write_invalid: bool = False) -> None:
        logging.info("Starting generation of queries")
        os.makedirs(self.settings.queries_path, exist_ok=True)
        os.makedirs(f"{self.settings.queries_path}/lists", exist_ok=True)
        os.makedirs(f"{self.settings.queries_path}/objects", exist_ok=True)
        for definition in self.ast.definitions:
            if isinstance(definition, ObjectTypeDefinitionNode):
                type_name = definition.name.value
                if type_name not in include_definitions:
                    continue
                for field in definition.fields:
                    if not self.is_deprecated(field):
                        query_name = field.name.value
                        ultimate_object = self.find_ultimate_object(self.get_field_type_name(field.type))
                        if ultimate_object in self.field_type_rules["exclude"]:
                            logging.info(f"Skipping query for {query_name} as it ultimately returns an excluded type {ultimate_object}")
                            continue
                        if (not included_queries or query_name in included_queries) and (not excluded_queries or query_name not in excluded_queries):
                            max_depth = self.max_depth_overrides.get(query_name, self.default_max_depth)
                            query_ast = self.generate_query_with_variables_ast(query_name, field, 0, max_depth)
                            query_str = print_ast(query_ast)

                            validation_errors = validate(
                                schema=self.schema,
                                document_ast=parse(query_str),
                                rules=specified_rules,
                            )

                            if validation_errors:
                                for error in validation_errors:
                                    logging.error(f"Validation error in query {query_name}: {error.message}")
                                if not write_invalid:
                                    continue
                            else:
                                logging.info(f"All validations passed for query {query_name}")
                            
                            # Determine the output directory
                            if query_name in self.list_returning_queries:
                                output_dir = f"{self.settings.queries_path}/lists"
                            else:
                                output_dir = f"{self.settings.queries_path}/objects"
                        
                            # Save the query to a file
                            output_file = f"{output_dir}/{query_name}.graphql"
                            with open(output_file, "w") as f:
                                f.write(query_str)
                            logging.info(f"Generated query for {query_name} and saved to {output_file}")
                            
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
    query_generator.shopify_generate_queries(included_queries=["orders"],write_invalid=True)
