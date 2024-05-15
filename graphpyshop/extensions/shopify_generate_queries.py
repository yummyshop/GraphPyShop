from typing import Optional
import orjson
from graphql import build_client_schema, print_schema, parse, print_ast
from graphql.language import ast as graphql_ast
import os
import logging
from ariadne_codegen.main import client
from ariadne_codegen.config import get_config_dict, get_graphql_schema_settings, get_client_settings
from ariadne_codegen.schema import get_graphql_schema_from_path, get_graphql_schema_from_url

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config_dict = get_config_dict()
settings = get_client_settings(config_dict)
"""
if settings.schema_path:
    schema = get_graphql_schema_from_path(settings.schema_path)
else:
    schema = get_graphql_schema_from_url(
        url=settings.remote_schema_url,
        headers=settings.remote_schema_headers,
        verify_ssl=settings.remote_schema_verify_ssl,
    )
"""
schema = get_graphql_schema_from_path('/Users/salomartin/Development/yummy/GraphPyShop/graphpyshop/extensions/schema.graphql')

# Step 3: Convert to AST
sdl = print_schema(schema)
with open('/Users/salomartin/Development/yummy/GraphPyShop/graphpyshop/extensions/schema.graphql', 'w') as schema_file:
    schema_file.write(sdl)
ast = parse(sdl)

max_depth = 2

# Step 4: Inspect the AST and generate queries
output_dir = '/Users/salomartin/Development/yummy/GraphPyShop/graphpyshop/extensions/generated_queries'
os.makedirs(output_dir, exist_ok=True)

def is_deprecated(field: graphql_ast.FieldDefinitionNode) -> bool:
    return any(directive.name.value == 'deprecated' for directive in field.directives)

def get_field_type(field_type: graphql_ast.TypeNode) -> graphql_ast.TypeNode:
    while isinstance(field_type, (graphql_ast.NonNullTypeNode, graphql_ast.ListTypeNode)):
        field_type = field_type.type
    return field_type

def get_field_type_name(field_type: graphql_ast.TypeNode) -> str:
    while isinstance(field_type, (graphql_ast.NonNullTypeNode, graphql_ast.ListTypeNode)):
        field_type = field_type.type
    return field_type.name.value if isinstance(field_type, graphql_ast.NamedTypeNode) else ""


def get_ultimate_object(type_node: graphql_ast.TypeNode) -> str:
    while isinstance(type_node, (graphql_ast.NonNullTypeNode, graphql_ast.ListTypeNode)):
        type_node = type_node.type
    if isinstance(type_node, graphql_ast.NamedTypeNode):
        return type_node.name.value
    return ""

def find_ultimate_object(type_name: str) -> str:
    for definition in ast.definitions:
        if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == type_name:
            for field in definition.fields:
                field_type = get_field_type(field.type)
                if isinstance(field_type, graphql_ast.ObjectTypeDefinitionNode):
                    for sub_field in field_type.fields:
                        if sub_field.name.value == "node":
                            return get_ultimate_object(sub_field.type)
                elif field.name.value == "nodes":
                    return get_ultimate_object(field.type)
    return type_name

list_returning_queries = {}

def returns_a_list(field: graphql_ast.FieldDefinitionNode) -> bool:
    field_type_name = get_field_type_name(field.type)
    return field_type_name.endswith("Connection") or isinstance(field.type, graphql_ast.ListTypeNode)

for definition in ast.definitions:
    if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == "QueryRoot":
        for field in definition.fields:
            if not is_deprecated(field):
                field_type_name = get_field_type_name(field.type)
                ultimate_object = find_ultimate_object(field_type_name)
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

import json

def print_indented_json(data, indent=4):
    print(json.dumps(data, indent=indent))



scalar_types = {definition.name.value for definition in ast.definitions if isinstance(definition, graphql_ast.ScalarTypeDefinitionNode)}
enum_types = {definition.name.value for definition in ast.definitions if isinstance(definition, graphql_ast.EnumTypeDefinitionNode)}

def is_core_type(type_name: str) -> bool:
    core_types = {"String", "Int", "Float", "Boolean", "ID", "DateTime", "UnsignedInt64"}
    return type_name in core_types or type_name in scalar_types or type_name in enum_types

# TODO: Avoid putting an array of order id's to customer if it an order has one to many to customer

def generate_query_ast(query_name: str, field: graphql_ast.FieldDefinitionNode, visited_types: dict[str, int], depth: int = 0, max_depth: int = max_depth, parent: Optional[graphql_ast.FieldDefinitionNode] = None, path: str = "") -> graphql_ast.SelectionSetNode:
    current_path = f"{path} > {field.name.value}" if path else field.name.value
    field_type_name = get_field_type_name(field.type)
    ultimate_field_type_name = find_ultimate_object(field_type_name)
    query_return_type = list_returning_queries[query_name]

    logging.info(f"[{query_name}][{current_path}][depth: {depth}] Generating query AST for field: {current_path}, depth: {depth}")
    
    # Are there any fields with only nodes without edges?
    if field.name.value == 'nodes':
        logging.info(f"[{query_name}][{current_path}][depth: {depth}] Skipping all nodes fields as we traverse only edges > node")
        return graphql_ast.SelectionSetNode(selections=[])
    
    if depth > max_depth:
        logging.info(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached. Returning empty selection set.")
        return graphql_ast.SelectionSetNode(selections=[])

    if is_deprecated(field):
        logging.info(f"[{query_name}][{current_path}][depth: {depth}] Field {field.name.value} is deprecated. Skipping.")
        return graphql_ast.SelectionSetNode(selections=[])
    
    # This also removes connections like lastOrder, which is actually fine as it keeps the data nesting sane
    if ultimate_field_type_name in list_returning_queries_by_type:
        if ultimate_field_type_name in direct_object_references and query_return_type in direct_object_references[ultimate_field_type_name]:
            logging.warning(f"[{query_name}][{current_path}][depth: {depth}] Skipping field as it matches direct object reference.")
            return graphql_ast.SelectionSetNode(selections=[])

    selections: List[Union[graphql_ast.FieldNode, graphql_ast.InlineFragmentNode]] = []
    # Check if the field has children (fields)
    has_children = False
    for definition in ast.definitions:
        if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == get_field_type_name(field.type):
            has_children = True
            logging.info(f"[{query_name}][{current_path}][depth: {depth}] Processing ObjectTypeDefinitionNode: {definition.name.value}")

            for sub_field in definition.fields:
                if not is_deprecated(sub_field):
                    # Do not count depth if the field is named 'edges' or 'node'
                    new_depth = depth if sub_field.name.value in {"edges", "node", "pageInfo"} else depth + 1

                    sub_query = generate_query_ast(query_name, sub_field, visited_types, new_depth, max_depth, field, current_path)

                    # Check if the sub_query has any selections and would exceed max depth
                    if len(sub_query.selections) == 0 and not is_core_type(get_field_type_name(sub_field.type)):
                        logging.warning(f"[{query_name}][{current_path}][depth: {depth}] Field {sub_field.name.value} should have children but doesn't. Returning empty selection set.")
                        continue

                    if new_depth > max_depth:
                        logging.warning(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached for field {sub_field.name.value}. Skipping this field.")
                        continue
                    

                    subfield_type_name = get_field_type_name(sub_field.type)
                    if field_type_name in list_returning_queries_by_type and depth != 0 and subfield_type_name != "ID":
                        logging.info(f"[{query_name}][{current_path}][depth: {depth}] It's a list returning field and type is not id, returning empty set")
                        continue
                    
                    selections.append(graphql_ast.FieldNode(
                        name=graphql_ast.NameNode(value=sub_field.name.value),
                        selection_set=sub_query
                    ))
            break

    if not has_children:
        # Check if the field type is an interface and find all implementing types
        for definition in ast.definitions:
            if isinstance(definition, graphql_ast.InterfaceTypeDefinitionNode) and definition.name.value == field_type_name:
                has_children = True
                logging.info(f"[{query_name}][{current_path}][depth: {depth}] Processing InterfaceTypeDefinitionNode: {definition.name.value}")

                for object_definition in ast.definitions:
                    if isinstance(object_definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value in [interface.name.value for interface in object_definition.interfaces]:
                        logging.info(f"[{query_name}][{current_path}][depth: {depth}] Found implementing type: {object_definition.name.value}")

                        fragment_selections = []
                        for sub_field in object_definition.fields:
                            if not is_deprecated(sub_field):
                                new_depth = depth if sub_field.name.value in {"edges", "node", "pageInfo"} else depth + 1

                                sub_query = generate_query_ast(query_name, sub_field, visited_types, new_depth, max_depth, field, current_path)

                                if len(sub_query.selections) == 0 and not is_core_type(get_field_type_name(sub_field.type)):
                                    logging.warning(f"[{query_name}][{current_path}][depth: {depth}] Field {sub_field.name.value} should have children but doesn't. Returning empty selection set.")
                                    continue

                                if new_depth > max_depth:
                                    logging.warning(f"[{query_name}][{current_path}][depth: {depth}] Max depth reached for field {sub_field.name.value}. Skipping this field.")
                                    continue

                                subfield_type_name = get_field_type_name(sub_field.type)
                                if field_type_name in list_returning_queries_by_type and depth != 0 and subfield_type_name != "ID":
                                    logging.info(f"[{query_name}][{current_path}][depth: {depth}] It's a list returning field and type is not id, returning empty set")
                                    continue

                                fragment_selections.append(graphql_ast.FieldNode(
                                    name=graphql_ast.NameNode(value=sub_field.name.value),
                                    selection_set=sub_query
                                ))

                        if fragment_selections:
                            selections.append(graphql_ast.InlineFragmentNode(
                                type_condition=graphql_ast.NamedTypeNode(name=graphql_ast.NameNode(value=object_definition.name.value)),
                                selection_set=graphql_ast.SelectionSetNode(selections=fragment_selections)
                            ))

    if not has_children:
        logging.info(f"[{query_name}][{current_path}][depth: {depth}] Field {field.name.value} has no children. Skipping nested selection.")
        return graphql_ast.SelectionSetNode(selections=[])

    # Remove 'node', 'nodes', and 'pageInfo' if 'edges' is present
    if any(isinstance(selection, graphql_ast.FieldNode) and selection.name.value == "edges" for selection in selections):
        selections = [selection for selection in selections if not (isinstance(selection, graphql_ast.FieldNode) and selection.name.value in {"nodes"})]

    logging.info(f"[{query_name}][{current_path}][depth: {depth}] Returning selection set with {len(selections)} selections.")
    return graphql_ast.SelectionSetNode(selections=selections)

def generate_query_with_variables_ast(query_name: str, field: graphql_ast.FieldDefinitionNode, visited_types: dict[str, int], depth: int = 0, max_depth: int = max_depth) -> graphql_ast.OperationDefinitionNode:
    query_fields = generate_query_ast(query_name, field, visited_types, depth, max_depth)
    variable_definitions = []
    arguments = []

    for arg in field.arguments:
        arg_type_name = get_field_type_name(arg.type)
        variable_definitions.append(graphql_ast.VariableDefinitionNode(
            variable=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=arg.name.value)),
            type=arg.type
        ))
        arguments.append(graphql_ast.ArgumentNode(
            name=graphql_ast.NameNode(value=arg.name.value),
            value=graphql_ast.VariableNode(name=graphql_ast.NameNode(value=arg.name.value))
        ))

    # Include the root field in the selection set with arguments
    root_field = graphql_ast.FieldNode(
        name=graphql_ast.NameNode(value=field.name.value),
        arguments=arguments,
        selection_set=query_fields
    )

    return graphql_ast.OperationDefinitionNode(
        operation=graphql_ast.OperationType.QUERY,
        name=graphql_ast.NameNode(value=field.name.value),
        variable_definitions=variable_definitions,
        selection_set=graphql_ast.SelectionSetNode(selections=[root_field])
    )


include_definitions = ['QueryRoot']
exclude_definitions: list[str] = []

for definition in ast.definitions:
    if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode):
        type_name = definition.name.value
        
        if type_name not in include_definitions:
            continue
        
        for field in definition.fields:
            if not is_deprecated(field):
                query_name = field.name.value
                if query_name == "customers":
                    visited_types: dict[str, int] = {}
                    query_ast = generate_query_with_variables_ast(query_name, field, visited_types)
                    query_str = print_ast(query_ast)

                    # Save the query to a file
                    output_file = f"{output_dir}/{query_name}.graphql"
                    with open(output_file, "w") as f:
                        f.write(query_str)
                    logging.info(f"Generated query for {query_name} and saved to {output_file}")
                    print(query_str)



print("direct_object_references:")
print_indented_json(direct_object_references)

print("list_returning_queries_by_type:")
print_indented_json(list_returning_queries_by_type)

print("list_returning_queries:")
print_indented_json(list_returning_queries)