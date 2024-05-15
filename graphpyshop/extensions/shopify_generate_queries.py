from typing import Any, Dict, Optional, List, Union
from graphql import print_schema, parse, print_ast
from graphql.language import ast as graphql_ast
import os
import logging
from ariadne_codegen.config import get_client_settings, get_config_dict
from ariadne_codegen.schema import get_graphql_schema_from_path, get_graphql_schema_from_url
from functools import lru_cache

def is_deprecated(field: graphql_ast.FieldDefinitionNode) -> bool:
    return any(directive.name.value == 'deprecated' for directive in field.directives)

def get_field_type(field_type: graphql_ast.TypeNode) -> graphql_ast.TypeNode:
    while isinstance(field_type, (graphql_ast.NonNullTypeNode, graphql_ast.ListTypeNode)):
        field_type = field_type.type
    return field_type

@lru_cache(maxsize=None)
def get_field_type_name(field_type: graphql_ast.TypeNode) -> str:
    while isinstance(field_type, (graphql_ast.NonNullTypeNode, graphql_ast.ListTypeNode)):
        field_type = field_type.type
    return field_type.name.value if isinstance(field_type, graphql_ast.NamedTypeNode) else ""

@lru_cache(maxsize=None)
def get_ultimate_object(type_node: graphql_ast.TypeNode) -> str:
    while isinstance(type_node, (graphql_ast.NonNullTypeNode, graphql_ast.ListTypeNode)):
        type_node = type_node.type
    if isinstance(type_node, graphql_ast.NamedTypeNode):
        return type_node.name.value
    return ""

def find_ultimate_object(type_name: str, ast: graphql_ast.DocumentNode) -> str:
    for definition in ast.definitions:
        if isinstance(definition, (graphql_ast.ObjectTypeDefinitionNode, graphql_ast.UnionTypeDefinitionNode)) and definition.name.value == type_name:
            if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode):
                for field in definition.fields:
                    field_type = get_field_type(field.type)
                    if isinstance(field_type, graphql_ast.ObjectTypeDefinitionNode):
                        for sub_field in field_type.fields:
                            if sub_field.name.value == "node":
                                return get_ultimate_object(sub_field.type)
                    elif field.name.value == "nodes":
                        return get_ultimate_object(field.type)
            elif isinstance(definition, graphql_ast.UnionTypeDefinitionNode):
                for type_ in definition.types:
                    return type_.name.value
    return type_name

def returns_a_list(field: graphql_ast.FieldDefinitionNode) -> bool:
    field_type_name = get_field_type_name(field.type)
    return field_type_name.endswith("Connection") or isinstance(field.type, graphql_ast.ListTypeNode)

# Data structure to store field name or type exclusions or inclusions
field_type_rules: Dict[str, Dict[str, List[str]]] = {
    "include": {"App": ["ID"], "CommentEventSubject": ["ID"]},
    "exclude": {"StoreCreditAccount": [], "Market": [], "MetafieldDefinitionConnection": [],"HasMetafields": [], "StaffMember": []}
}

field_name_rules: Dict[str, List[str]] = {
    "include": [],
    "exclude": ["legacyResourceId", "nodes", "metafield", "metafieldsByIdentifiers","exchangeV2s","originalSource","hasCollection"]
}

# Example relations
def generate_query_ast(query_name: str, field: graphql_ast.FieldDefinitionNode, visited_types: Dict[str, int], depth: int, max_depth: int, ast: graphql_ast.DocumentNode, list_returning_queries: Dict[str, str], list_returning_queries_by_type: Dict[str, Any], direct_object_references: Dict[str, List[str]], hardcoded_defaults: Dict[str, Any], scalar_types: set, enum_types: set, parent: Optional[graphql_ast.FieldDefinitionNode] = None, path: str = "", variables: Dict[str, graphql_ast.VariableDefinitionNode] = {}, field_type_rules: Dict[str, Dict[str, List[str]]] = field_type_rules, field_name_rules: Dict[str, List[str]] = field_name_rules) -> graphql_ast.SelectionSetNode:
    current_path = f"{path} > {field.name.value}" if path else field.name.value
    field_type_name = get_field_type_name(field.type)
    ultimate_field_type_name = find_ultimate_object(field_type_name, ast)
    query_return_type = list_returning_queries.get(query_name, None)
    
    # Are there any fields with only nodes without edges?
    if field.name.value in field_name_rules["exclude"]:
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping field {field.name.value} as it is in the exclude list")
        return graphql_ast.SelectionSetNode(selections=[])
    
    if ultimate_field_type_name in field_type_rules["exclude"].keys():
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping as it's an excluded field")
        return graphql_ast.SelectionSetNode(selections=[])

    if depth > max_depth:
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached. Returning empty selection set.")
        return graphql_ast.SelectionSetNode(selections=[])

    if is_deprecated(field):
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {field.name.value} is deprecated. Skipping.")
        return graphql_ast.SelectionSetNode(selections=[])
    
    # Exclude fields with required non-null arguments unless it's depth 0
    if depth != 0 and any(isinstance(arg.type, graphql_ast.NonNullTypeNode) for arg in field.arguments):
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping field {field.name.value} as it has required non-null arguments")
        return graphql_ast.SelectionSetNode(selections=[])

    # This also removes connections like lastOrder, which is actually fine as it keeps the data nesting sane
    if ultimate_field_type_name in list_returning_queries_by_type:
        if ultimate_field_type_name in direct_object_references and query_return_type in direct_object_references[ultimate_field_type_name]:
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Skipping field as it matches direct object reference.")
            return graphql_ast.SelectionSetNode(selections=[])

    selections: List[Union[graphql_ast.FieldNode, graphql_ast.InlineFragmentNode]] = []
    # Check if the field has children (fields)
    has_children = False
    for definition in ast.definitions:
        if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == get_field_type_name(field.type):
            has_children = True
            logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Processing ObjectTypeDefinitionNode: {definition.name.value}")

            for sub_field in definition.fields:
                if not is_deprecated(sub_field):
                    # Do not count depth if the field is named 'edges' or 'node'
                    new_depth = depth if sub_field.name.value in {"edges", "node", "pageInfo"} else depth + 1

                    sub_query = generate_query_ast(query_name, sub_field, visited_types, new_depth, max_depth, ast, list_returning_queries, list_returning_queries_by_type, direct_object_references, hardcoded_defaults, scalar_types, enum_types, field, current_path, variables, field_type_rules, field_name_rules)

                    # Check if the sub_query has any selections and would exceed max depth
                    if len(sub_query.selections) == 0 and not is_core_type(get_field_type_name(sub_field.type), scalar_types, enum_types):
                        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {sub_field.name.value} should have children but doesn't. Returning empty selection set.")
                        continue

                    if new_depth > max_depth:
                        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached for field {sub_field.name.value}. Skipping this field.")
                        continue
                    
                    subfield_type_name = get_field_type_name(sub_field.type)
                    if field_type_name != "Metafield" and field_type_name in list_returning_queries_by_type and depth != 0 and subfield_type_name != "ID":
                        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] It's a list returning field and type is not id, returning empty set")
                        continue

                    if field_type_name in field_type_rules["include"] and subfield_type_name not in field_type_rules["include"][field_type_name]:
                        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field type {field_type_name} includes subfield type {subfield_type_name}, returning empty set")
                        continue
                    
                    sub_arguments = []
                    for arg in sub_field.arguments:
                        arg_type_name = get_field_type_name(arg.type)
                        variable_name = arg.name.value
                        if variable_name in variables:
                            variable_name = f"{sub_field.name.value}_{arg.name.value}_{field_type_name}"
                        if variable_name not in variables:
                            default_value = hardcoded_defaults.get(arg.name.value, arg.default_value)
                            variables[variable_name] = graphql_ast.VariableDefinitionNode(
                                variable=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=variable_name)),
                                type=arg.type,
                                default_value=default_value
                            )
                        sub_arguments.append(graphql_ast.ArgumentNode(
                            name=graphql_ast.NameNode(value=arg.name.value),
                            value=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=variable_name))
                        ))

                    selections.append(graphql_ast.FieldNode(
                        name=graphql_ast.NameNode(value=sub_field.name.value),
                        arguments=sub_arguments,
                        selection_set=sub_query
                    ))
            # Add __typename to the selection set only if there are selections
            if selections:
                selections.append(graphql_ast.FieldNode(
                    name=graphql_ast.NameNode(value="__typename")
                ))
            break

    if not has_children:
        # Check if the field type is an interface or union and find all implementing types
        for definition in ast.definitions:
            if isinstance(definition, (graphql_ast.InterfaceTypeDefinitionNode, graphql_ast.UnionTypeDefinitionNode)) and definition.name.value == field_type_name:
                has_children = True
                logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Processing {type(definition).__name__}: {definition.name.value}")

                for object_definition in ast.definitions:
                    if isinstance(object_definition, graphql_ast.ObjectTypeDefinitionNode) and (
                        definition.name.value in [interface.name.value for interface in object_definition.interfaces] or
                        definition.name.value in [union_type.name.value for union_type in getattr(object_definition, 'types', [])]
                    ):
                        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Found implementing type: {object_definition.name.value}")

                        fragment_selections = []
                        for sub_field in object_definition.fields:
                            if not is_deprecated(sub_field):
                                new_depth = depth if sub_field.name.value in {"edges", "node", "pageInfo"} else depth + 1

                                sub_query = generate_query_ast(query_name, sub_field, visited_types, new_depth, max_depth, ast, list_returning_queries, list_returning_queries_by_type, direct_object_references, hardcoded_defaults, scalar_types, enum_types, field, current_path, variables, field_type_rules, field_name_rules)

                                if len(sub_query.selections) == 0 and not is_core_type(get_field_type_name(sub_field.type), scalar_types, enum_types):
                                    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {sub_field.name.value} should have children but doesn't. Returning empty selection set.")
                                    continue

                                if new_depth > max_depth:
                                    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached for field {sub_field.name.value}. Skipping this field.")
                                    continue

                                subfield_type_name = get_field_type_name(sub_field.type)
                                if field_type_name != "Metafield" and field_type_name in list_returning_queries_by_type and depth != 0 and subfield_type_name != "ID":
                                    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] It's a list returning field and type is not id, returning empty set")
                                    continue

                                if field_type_name in field_type_rules["include"] and subfield_type_name not in field_type_rules["include"][field_type_name]:
                                    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field type {field_type_name} includes subfield type {subfield_type_name}, returning empty set")
                                    continue

                                sub_arguments = []
                                for arg in sub_field.arguments:
                                    arg_type_name = get_field_type_name(arg.type)
                                    variable_name = arg.name.value
                                    if variable_name in variables:
                                        variable_name = f"{sub_field.name.value}_{arg.name.value}_{field_type_name}"
                                    if variable_name not in variables:
                                        default_value = hardcoded_defaults.get(arg.name.value, arg.default_value)
                                        variables[variable_name] = graphql_ast.VariableDefinitionNode(
                                            variable=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=variable_name)),
                                            type=arg.type,
                                            default_value=default_value
                                        )
                                    sub_arguments.append(graphql_ast.ArgumentNode(
                                        name=graphql_ast.NameNode(value=arg.name.value),
                                        value=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=variable_name))
                                    ))

                                fragment_selections.append(graphql_ast.FieldNode(
                                    name=graphql_ast.NameNode(value=sub_field.name.value),
                                    arguments=sub_arguments,
                                    selection_set=sub_query
                                ))

                        if fragment_selections:
                            # Add __typename to the fragment selection set
                            if fragment_selections:
                                fragment_selections.append(graphql_ast.FieldNode(
                                    name=graphql_ast.NameNode(value="__typename")
                                ))
                            selections.append(graphql_ast.InlineFragmentNode(
                                type_condition=graphql_ast.NamedTypeNode(name=graphql_ast.NameNode(value=object_definition.name.value)),
                                selection_set=graphql_ast.SelectionSetNode(selections=fragment_selections)
                            ))

    if not has_children:
        logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Field {field.name.value} has no children. Skipping nested selection.")
        return graphql_ast.SelectionSetNode(selections=[])

    # Remove 'node', 'nodes', and 'pageInfo' if 'edges' is present
    if any(isinstance(selection, graphql_ast.FieldNode) and selection.name.value == "edges" for selection in selections):
        selections = [selection for selection in selections if not (isinstance(selection, graphql_ast.FieldNode) and selection.name.value in {"nodes"})]

    logging.debug(f"[{query_name}][{current_path}][depth: {depth}] Returning selection set with {len(selections)} selections.")
    return graphql_ast.SelectionSetNode(selections=selections)

def generate_query_with_variables_ast(query_name: str, field: graphql_ast.FieldDefinitionNode, visited_types: Dict[str, int], depth: int, max_depth: int, ast: graphql_ast.DocumentNode, list_returning_queries: Dict[str, str], list_returning_queries_by_type: Dict[str, Any], direct_object_references: Dict[str, List[str]], hardcoded_defaults: Dict[str, Any], scalar_types: set, enum_types: set, field_type_rules: Dict[str, Dict[str, List[str]]] = field_type_rules, field_name_rules: Dict[str, List[str]] = field_name_rules) -> graphql_ast.OperationDefinitionNode:
    variables: Dict[str, graphql_ast.VariableDefinitionNode] = {}
    query_fields = generate_query_ast(query_name, field, visited_types, depth, max_depth, ast, list_returning_queries, list_returning_queries_by_type, direct_object_references, hardcoded_defaults, scalar_types, enum_types, variables=variables, field_type_rules=field_type_rules, field_name_rules=field_name_rules)
    variable_definitions: List[graphql_ast.VariableDefinitionNode] = list(variables.values())
    arguments: List[graphql_ast.ArgumentNode] = []

    for arg in field.arguments:
        arg_type_name = get_field_type_name(arg.type)
        variable_name = arg.name.value
        if variable_name in variables:
            variable_name = f"{field.name.value}_{arg.name.value}_{get_field_type_name(field.type)}"
        if variable_name not in variables:
            default_value = hardcoded_defaults.get(arg.name.value, arg.default_value)
            variables[variable_name] = graphql_ast.VariableDefinitionNode(
                variable=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=variable_name)),
                type=arg.type,
                default_value=default_value
            )
        arguments.append(graphql_ast.ArgumentNode(
            name=graphql_ast.NameNode(value=arg.name.value),
            value=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=variable_name))
        ))

    # Include the root field in the selection set with arguments
    root_field = graphql_ast.FieldNode(
        name=graphql_ast.NameNode(value=field.name.value),
        arguments=arguments,
        selection_set=query_fields
    )

    # Create a list of variable definitions for the top-level query
    top_level_variable_definitions: List[graphql_ast.VariableDefinitionNode] = [
        graphql_ast.VariableDefinitionNode(
            variable=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=var_name)),
            type=var_def.type,
            default_value=var_def.default_value
        )
        for var_name, var_def in variables.items()
    ]

    return graphql_ast.OperationDefinitionNode(
        operation=graphql_ast.OperationType.QUERY,
        name=graphql_ast.NameNode(value=field.name.value),
        variable_definitions=top_level_variable_definitions,
        selection_set=graphql_ast.SelectionSetNode(selections=[root_field])
    )
def is_core_type(type_name: str, scalar_types: set, enum_types: set) -> bool:
    core_types = {"String", "Int", "Float", "Boolean", "ID", "DateTime", "UnsignedInt64"}
    return type_name in core_types or type_name in scalar_types or type_name in enum_types


def shopify_generate_queries(config_dict: Dict[str, Any], include_definitions: List[str] = ['QueryRoot'], exclude_definitions: List[str] = [], included_queries: List[str] = [], excluded_queries: List[str] = ["node", "nodes", "metafields", "job"], field_type_rules: Dict[str, Dict[str, List[str]]] = field_type_rules, field_name_rules: Dict[str, List[str]] = field_name_rules) -> None:
    logging.info("Starting generation of queries")
    settings = get_client_settings(config_dict)
    if settings.schema_path:
        logging.info(f"Loading schema from path: {settings.schema_path}")
        schema = get_graphql_schema_from_path(settings.schema_path)
    else:
        logging.info(f"Loading schema from URL: {settings.remote_schema_url}")
        schema = get_graphql_schema_from_url(
            url=settings.remote_schema_url,
            headers=settings.remote_schema_headers,
            verify_ssl=settings.remote_schema_verify_ssl,
        )

    # Step 3: Convert to AST
    sdl = print_schema(schema)

    # Check if the flag is set to write the schema to a file
    if config_dict.get("write_schema_to_file", False):
        with open(f"{settings.target_package_path}/schema.graphql", 'w') as schema_file:
            schema_file.write(sdl)
        logging.info(f"Schema written to {settings.target_package_path}/schema.graphql")
            
    ast = parse(sdl)

    # Dictionary to store max_depth overrides
    max_depth_overrides = {
        "checkoutBranding": 4,
    }
    default_max_depth = 3

    list_returning_queries = {}
    for definition in ast.definitions:
        if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == "QueryRoot":
            for field in definition.fields:
                if not is_deprecated(field):
                    field_type_name = get_field_type_name(field.type)
                    ultimate_object = find_ultimate_object(field_type_name, ast)
                    if returns_a_list(field):
                        list_returning_queries[field.name.value] = ultimate_object

    list_returning_queries_by_type = {}
    for key, value in list_returning_queries.items():
        if value in list_returning_queries_by_type:
            if isinstance(list_returning_queries_by_type[value], list):
                list_returning_queries_by_type[value].append(key)
            else:
                list_returning_queries_by_type[value] = [list_returning_queries_by_type[value], key]
        else:
            list_returning_queries_by_type[value] = key

    # Create a dictionary to store the direct object references
    direct_object_references = {}
    for key in list_returning_queries_by_type:
        direct_references = set()
        for definition in ast.definitions:
            if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == key:
                for field in definition.fields:
                    field_type = get_field_type(field.type)
                    if isinstance(field_type, graphql_ast.NamedTypeNode) and field_type.name.value in list_returning_queries_by_type:
                        direct_references.add(field_type.name.value)
        if key == "MetafieldDefinition":
            for enum_definition in ast.definitions:
                if isinstance(enum_definition, graphql_ast.EnumTypeDefinitionNode) and enum_definition.name.value == "MetafieldOwnerType":
                    for enum_value in enum_definition.values:
                        formatted_value = ''.join(word.capitalize() for word in enum_value.name.value.split('_'))
                        direct_references.add(formatted_value)
        if direct_references:  # Only add the key if the list is not empty
            direct_object_references[key] = list(direct_references)

    # List of arguments with hardcoded defaults
    hardcoded_defaults: Dict[str, Any] = {
        "first": graphql_ast.IntValueNode(value="250")
    }

    scalar_types = {definition.name.value for definition in ast.definitions if isinstance(definition, graphql_ast.ScalarTypeDefinitionNode)}
    enum_types = {definition.name.value for definition in ast.definitions if isinstance(definition, graphql_ast.EnumTypeDefinitionNode)}

    os.makedirs(settings.queries_path, exist_ok=True)
    os.makedirs(f"{settings.queries_path}/lists", exist_ok=True)
    os.makedirs(f"{settings.queries_path}/objects", exist_ok=True)
    
    for definition in ast.definitions:
        if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode):
            type_name = definition.name.value

            if type_name not in include_definitions:
                continue

            for field in definition.fields:
                if not is_deprecated(field):
                    query_name = field.name.value
                    ultimate_object = find_ultimate_object(get_field_type_name(field.type), ast)
                    if ultimate_object in field_type_rules["exclude"]:
                        logging.info(f"Skipping query for {query_name} as it ultimately returns an excluded type {ultimate_object}")
                        continue
                    if (not included_queries or query_name in included_queries) and (not excluded_queries or query_name not in excluded_queries):
                        visited_types: dict[str, int] = {}
                        max_depth = max_depth_overrides.get(query_name, default_max_depth)
                        query_ast = generate_query_with_variables_ast(query_name, field, visited_types, 0, max_depth, ast, list_returning_queries, list_returning_queries_by_type, direct_object_references, hardcoded_defaults, scalar_types, enum_types)
                        query_str = print_ast(query_ast)

                        # Check if the query contains any fields
                        if not query_ast.selection_set.selections or not query_ast.selection_set.selections[0].selection_set.selections:
                            logging.error(f"Generated query for {query_name} contains no fields. Skipping file write.")
                            continue

                        # Determine the output directory
                        if query_name in list_returning_queries:
                            output_dir = f"{settings.queries_path}/lists"
                        else:
                            output_dir = f"{settings.queries_path}/objects"

                        # Save the query to a file
                        output_file = f"{output_dir}/{query_name}.graphql"
                        with open(output_file, "w") as f:
                            f.write(query_str)
                        logging.info(f"Generated query for {query_name} and saved to {output_file}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    config_dict = get_config_dict()
    config_dict["write_schema_to_file"] = True
    shopify_generate_queries(config_dict, included_queries=["orders"])
