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

if settings.schema_path:
    schema = get_graphql_schema_from_path(settings.schema_path)
else:
    schema = get_graphql_schema_from_url(
        url=settings.remote_schema_url,
        headers=settings.remote_schema_headers,
        verify_ssl=settings.remote_schema_verify_ssl,
    )
#schema = get_graphql_schema_from_path('/Users/salomartin/Development/yummy/GraphPyShop/graphpyshop/extensions/schema.graphql')

# Step 3: Convert to AST
sdl = print_schema(schema)
with open('/Users/salomartin/Development/yummy/GraphPyShop/graphpyshop/extensions/schema.graphql', 'w') as schema_file:
    schema_file.write(sdl)
ast = parse(sdl)

max_depth = 4

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

for definition in ast.definitions:
    if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == "QueryRoot":
        for field in definition.fields:
            if not is_deprecated(field):
                field_type_name = get_field_type_name(field.type)
                ultimate_object = find_ultimate_object(field_type_name)
                if field_type_name.endswith("Connection") or isinstance(field.type, graphql_ast.ListTypeNode):
                    list_returning_queries[field.name.value] = ultimate_object

reversed_queries = {}
for key, value in list_returning_queries.items():
    if value in reversed_queries:
        if isinstance(reversed_queries[value], list):
            reversed_queries[value].append(key)
        else:
            reversed_queries[value] = [reversed_queries[value], key]
    else:
        reversed_queries[value] = key
#TODO: Avoid implementing faulty one
#list_returning_queries = reversed_queries

def generate_query_ast(field: graphql_ast.FieldDefinitionNode, visited_types: dict[str, int], depth: int = 0, max_depth: int = max_depth, connection_count: int = 0, max_connections: int = 10) -> graphql_ast.SelectionSetNode:
    logging.info(f"Generating query AST for field: {field.name.value}, depth: {depth}, connection_count: {connection_count}")
    
    if depth > max_depth or connection_count >= max_connections:
        logging.info(f"Max depth or max connections reached. Returning empty selection set.")
        return graphql_ast.SelectionSetNode(selections=[])

    field_type = get_field_type(field.type)
    selections = []

    if isinstance(field_type, graphql_ast.NamedTypeNode):
        field_type_name = field_type.name.value
        logging.info(f"Field type is NamedTypeNode with name: {field_type_name}")
        
        if field_type_name in visited_types and visited_types[field_type_name] > 0:
            logging.info(f"Field type {field_type_name} already visited. Adding 'id' field to selections.")
            selections.append(graphql_ast.FieldNode(name=graphql_ast.NameNode(value="id")))
        else:
            visited_types[field_type_name] = visited_types.get(field_type_name, 0) + 1
            logging.info(f"Visiting field type {field_type_name}. Updated visited_types: {visited_types}")
            
            for definition in ast.definitions:
                if isinstance(definition, graphql_ast.ObjectTypeDefinitionNode) and definition.name.value == field_type_name:
                    logging.info(f"Processing ObjectTypeDefinitionNode: {definition.name.value}")
                    
                    for sub_field in definition.fields:
                        if not is_deprecated(sub_field):
                            sub_field_type = get_field_type(sub_field.type)
                            logging.info(f"Processing sub-field: {sub_field.name.value}, type: {sub_field_type}")
                            
                            if isinstance(sub_field_type, graphql_ast.NamedTypeNode):
                                sub_field_type_name = sub_field_type.name.value
                                logging.info(f"Sub-field type is NamedTypeNode with name: {sub_field_type_name}")
                                
                                if 'Connection' in sub_field_type_name:
                                    if connection_count < max_connections:
                                        logging.info(f"Sub-field type {sub_field_type_name} is a Connection. Generating sub-query.")
                                        sub_query = generate_query_ast(sub_field, visited_types, depth + 1, max_depth, connection_count + 1, max_connections)
                                        # Check if the ultimate object of the connection is in list_returning_queries
                                        ultimate_object = list_returning_queries.get(sub_field_type_name)
                                        if ultimate_object and ultimate_object in list_returning_queries:
                                            logging.info(f"Ultimate object {ultimate_object} is in list_returning_queries. Only adding 'id' field.")
                                            selections.append(graphql_ast.FieldNode(
                                                name=graphql_ast.NameNode(value=sub_field.name.value),
                                                selection_set=graphql_ast.SelectionSetNode(selections=[
                                                    graphql_ast.FieldNode(
                                                        name=graphql_ast.NameNode(value="edges"),
                                                        selection_set=graphql_ast.SelectionSetNode(selections=[
                                                            graphql_ast.FieldNode(
                                                                name=graphql_ast.NameNode(value="node"),
                                                                selection_set=graphql_ast.SelectionSetNode(selections=[
                                                                    graphql_ast.FieldNode(name=graphql_ast.NameNode(value="id"))
                                                                ])
                                                            )
                                                        ])
                                                    )
                                                ])
                                            ))
                                        else:
                                            selections.append(graphql_ast.FieldNode(
                                                name=graphql_ast.NameNode(value=sub_field.name.value),
                                                selection_set=graphql_ast.SelectionSetNode(selections=[
                                                    graphql_ast.FieldNode(
                                                        name=graphql_ast.NameNode(value="edges"),
                                                        selection_set=graphql_ast.SelectionSetNode(selections=[
                                                            graphql_ast.FieldNode(
                                                                name=graphql_ast.NameNode(value="node"),
                                                                selection_set=sub_query
                                                            )
                                                        ])
                                                    )
                                                ])
                                            ))
                                else:
                                    if sub_field_type_name in list_returning_queries:
                                        logging.info(f"Sub-field type {sub_field_type_name} is in list_returning_queries. Adding 'id' field to selections.")
                                        selections.append(graphql_ast.FieldNode(name=graphql_ast.NameNode(value="id")))
                                    elif sub_field_type_name not in visited_types or visited_types[sub_field_type_name] == 0:
                                        logging.info(f"Sub-field type {sub_field_type_name} is not a Connection. Generating sub-query.")
                                        sub_query = generate_query_ast(sub_field, visited_types, depth + 1, max_depth, connection_count, max_connections)
                                        selections.append(graphql_ast.FieldNode(
                                            name=graphql_ast.NameNode(value=sub_field.name.value),
                                            selection_set=sub_query
                                        ))
                            else:
                                logging.info(f"Sub-field {sub_field.name.value} is not a NamedTypeNode. Adding to selections.")
                                selections.append(graphql_ast.FieldNode(name=graphql_ast.NameNode(value=sub_field.name.value)))
            visited_types[field_type_name] -= 1
            logging.info(f"Finished processing field type {field_type_name}. Updated visited_types: {visited_types}")
    else:
        logging.info(f"Field type is not a NamedTypeNode. Adding field {field.name.value} to selections.")
        selections.append(graphql_ast.FieldNode(name=graphql_ast.NameNode(value=field.name.value)))

    # Remove 'node' and 'pageInfo' if 'edges' is present
    if any(selection.name.value == "edges" for selection in selections):
        selections = [selection for selection in selections if selection.name.value not in {"nodes", "pageInfo"}]

    logging.info(f"Returning selection set with {len(selections)} selections.")
    return graphql_ast.SelectionSetNode(selections=selections)

def generate_query_with_variables_ast(field: graphql_ast.FieldDefinitionNode, visited_types: dict[str, int], depth: int = 0, max_depth: int = max_depth, connection_count: int = 0, max_connections: int = 10) -> graphql_ast.OperationDefinitionNode:
    query_fields = generate_query_ast(field, visited_types, depth, max_depth, connection_count, max_connections)
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
                    query_ast = generate_query_with_variables_ast(field, visited_types)
                    query_str = print_ast(query_ast)

                    # Save the query to a file
                    output_file = f"{output_dir}/{query_name}.graphql"
                    with open(output_file, "w") as f:
                        f.write(query_str)
                    logging.info(f"Generated query for {query_name} and saved to {output_file}")
                    print(query_str)


