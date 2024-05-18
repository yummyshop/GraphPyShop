from typing import Callable, List, Union
import pytest
from graphql import parse, print_ast
from graphpyshop.extensions.shopify_generate_queries import ShopifyQueryGenerator
import subprocess
import tempfile

def gql(q: str) -> str:
    return q.strip()

@pytest.fixture
def generator() -> ShopifyQueryGenerator:
    return ShopifyQueryGenerator()


@pytest.fixture
def compare(generator: ShopifyQueryGenerator):
    def _compare(schema: str, expected_queries: Union[str, List[str]]):
        generator.set_schema(schema_override=schema)
        queries: List[str] = generator.generate_queries(return_queries=True) or []

        if isinstance(expected_queries, str):
            expected_queries = [expected_queries]

        for query, expected in zip(queries, expected_queries):
            actual_query = print_ast(parse(query))
            expected_query = print_ast(parse(expected))
            if actual_query != expected_query:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".graphql") as actual_file:
                    actual_file.write(actual_query.encode())
                    actual_file_path = actual_file.name

                with tempfile.NamedTemporaryFile(delete=False, suffix=".graphql") as expected_file:
                    expected_file.write(expected_query.encode())
                    expected_file_path = expected_file.name

                subprocess.run(["cursor", "--diff", actual_file_path, expected_file_path, "--reuse-window"])
                assert actual_query == expected_query
    return _compare

CompareType = Callable[[str, Union[str, List[str]]], None]

def test_basic_query_generation(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                shop: Shop
            }
            type Shop {
                name: String
            }
        """),
        gql("""
            query shop {
                shop {
                    name
                    __typename
                }
            }
        """),
    )

def test_query_with_optional_arguments(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                product(id: ID, name: String): Product
            }
            type Product {
                id: ID
                name: String
                price: Float
            }
        """),
        gql("""
            query product($product_id: ID, $product_name: String) {
                product(id: $product_id, name: $product_name) {
                    id
                    name
                    price
                    __typename
                }
            }
        """),
    )

def test_query_with_nested_fields(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                shop: Shop
            }
            type Shop {
                name: String
                owner: Owner
            }
            type Owner {
                name: String
                address: Address
            }
            type Address {
                street: String
                city: String
            }
        """),
        gql("""
            query shop {
                shop {
                    name
                    owner {
                        name
                        address {
                            street
                            city
                            __typename
                        }
                        __typename
                    }
                    __typename
                }
            }
        """),
    )

def test_query_with_list_field(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                products: [Product]
            }
            type Product {
                id: ID
                name: String
                price: Float
            }
        """),
        gql("""
            query products {
                products {
                    id
                    name
                    price
                    __typename
                }
            }
        """),
    )

def test_query_with_interface_field(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                searchResult: SearchResult
            }
            interface SearchResult {
                id: ID
                name: String
            }
            type Product implements SearchResult {
                id: ID
                name: String
                price: Float
            }
            type User implements SearchResult {
                id: ID
                name: String
                email: String
            }
        """),
        gql("""
            query searchResult {
                searchResult {
                    id
                    name
                    __typename
                    ... on Product {
                        price
                        __typename
                    }
                    ... on User {
                        email
                        __typename
                    }
                }
            }
        """),
    )

def test_query_with_enum_field(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                status: StatusEnum
            }
            enum StatusEnum {
                ACTIVE
                INACTIVE
                PENDING
            }
        """),
        gql("""
            query status {
                status
            }
        """),
    )

def test_query_excludes_deprecated_fields(compare: CompareType):
    compare(
        gql("""
            type Product {
                id: ID
                name: String
                price: Float
                oldField: String @deprecated(reason: "Use newField instead")
                newField: String
            }
        """),
        gql("""
            query product {
                product {
                    id
                    name
                    price
                    newField
                    __typename
                }
            }
        """),
    )

def test_skip_fields_with_required_non_null_args(compare: CompareType):
    compare(
        gql("""
            type QueryRoot {
                product: Product
            }
            type Product {
                id: ID
                name: String
                price: Float
                details(requiredArg: String!): String
            }
        """),
        gql("""
            query product {
                product {
                    id
                    name
                    price
                    __typename
                }
            }
        """),
    )

def test_query_with_custom_scalar(compare: CompareType):
    compare(
        gql("""
            scalar DateTime

            type Event {
                id: ID
                name: String
                startTime: DateTime
            }
        """),
        gql("""
            query event {
                event {
                    id
                    name
                    startTime
                    __typename
                }
            }
        """),
    )

def test_query_with_complex_arguments(compare: CompareType):
    compare(
        gql("""
            input ProductFilter {
                category: String
                priceRange: PriceRange
            }

            input PriceRange {
                min: Float
                max: Float
            }

            type QueryRoot {
                products(filter: ProductFilter): [Product]
            }

            type Product {
                id: ID
                name: String
                price: Float
            }
        """),
        gql("""
            query products($products_filter_ProductFilter: ProductFilter) {
                products(filter: $products_filter_ProductFilter) {
                    id
                    name
                    price
                    __typename
                }
            }
        """),
    )

def test_generate_mutation_query(compare: CompareType):
    compare(
        gql("""
            type Mutation {
                addUser(name: String!, age: Int!): User
            }

            type User {
                id: ID
                name: String
                age: Int
            }
        """),
        gql("""
            mutation {
                addUser(name: "John Doe", age: 30) {
                    id
                    name
                    age
                    __typename
                }
            }
        """),
    )

def test_query_with_deeply_nested_fields(compare: CompareType):
    compare(
        gql("""
            type Company {
                id: ID
                name: String
                employees: [Employee]
            }

            type Employee {
                id: ID
                name: String
                position: String
                projects: [Project]
            }

            type Project {
                id: ID
                name: String
                deadline: String
            }
        """),
        gql("""
            query {
                company {
                    id
                    name
                    employees {
                        id
                        name
                        position
                        projects {
                            id
                            name
                            deadline
                            __typename
                        }
                        __typename
                    }
                    __typename
                }
            }
        """),
    )

def test_query_with_self_referencing_type(compare: CompareType):
    compare(
        gql("""
            type Person {
                id: ID
                name: String
                friends: [Person]
            }
        """),
        gql("""
            query {
                person {
                    id
                    name
                    friends {
                        id
                        name
                        friends {
                            id
                            name
                            __typename
                        }
                        __typename
                    }
                    __typename
                }
            }
        """),
    )

def test_query_with_custom_directives(compare: CompareType):
    compare(
        gql("""
            directive @customDirective on FIELD_DEFINITION

            type Employee {
                id: ID
                name: String
                position: String @customDirective
                projects: [Project]
            }

            type Project {
                id: ID
                name: String
                deadline: String
            }
        """),
        gql("""
            query {
                employee {
                    id
                    name
                    position
                    projects {
                        id
                        name
                        deadline
                        __typename
                    }
                    __typename
                }
            }
        """),
    )

def test_query_with_union_types(compare: CompareType):
    compare(
        gql("""
            union SearchResult = Product | User

            type Product {
                id: ID
                name: String
                price: Float
            }

            type User {
                id: ID
                name: String
                email: String
            }
        """),
        gql("""
            query searchResult {
                searchResult {
                    ... on Product {
                        id
                        name
                        price
                        __typename
                    }
                    ... on User {
                        id
                        name
                        email
                        __typename
                    }
                    __typename
                }
            }
        """),
    )

def test_query_with_combined_interfaces_and_unions(compare: CompareType):
    compare(
        gql("""
            interface Identifiable {
                id: ID
            }

            type Book implements Identifiable {
                id: ID
                title: String
                author: String
            }

            type Movie implements Identifiable {
                id: ID
                title: String
                director: String
            }

            union Media = Book | Movie

            type QueryRoot {
                media: [Media]
            }
        """),
        gql("""
            query media {
                media {
                    ... on Book {
                        id
                        title
                        author
                        __typename
                    }
                    ... on Movie {
                        id
                        title
                        director
                        __typename
                    }
                    __typename
                }
            }
        """),
    )

def test_query_with_arguments_in_combined_interfaces_and_unions(compare: CompareType):
    compare(
        gql("""
            interface Identifiable {
                id: ID
            }

            type Book implements Identifiable {
                id: ID
                title: String
                author: String
            }

            type Movie implements Identifiable {
                id: ID
                title: String
                director: String
            }

            union Media = Book | Movie

            type QueryRoot {
                media(type: String): [Media]
            }
        """),
        gql("""
            query media($media_type: String) {
                media(type: $media_type) {
                    ... on Book {
                        id
                        title
                        author
                        __typename
                    }
                    ... on Movie {
                        id
                        title
                        director
                        __typename
                    }
                    __typename
                }
            }
        """),
    )


def test_query_with_conflicting_field_types_in_unions(compare: CompareType):
    compare(
        gql("""
            interface Identifiable {
                id: ID
            }

            type Book implements Identifiable {
                id: ID
                title: String
                author: String
            }

            type Movie implements Identifiable {
                id: ID
                title: String
                director: String
            }

            type VideoGame implements Identifiable {
                id: ID
                title: Int  # Conflicting type with Book and Movie
                developer: String
            }

            union Media = Book | Movie | VideoGame

            type QueryRoot {
                media(type: String): [Media]
            }
        """),
        gql("""
            query media($media_type: String) {
                media(type: $media_type) {
                    ... on Book {
                        id
                        bookTitle: title
                        author
                        __typename
                    }
                    ... on Movie {
                        id
                        movieTitle: title
                        director
                        __typename
                    }
                    ... on VideoGame {
                        id
                        videoGameTitle: title
                        developer
                        __typename
                    }
                    __typename
                }
            }
        """),
    )


#TODO: Write tests that are testing of max depth limiting, both before and after hitting it across all scenarios above