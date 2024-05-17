from typing import Callable, List, Union
import pytest
from graphql import parse, print_ast
from graphpyshop.extensions.shopify_generate_queries import ShopifyQueryGenerator

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
            assert print_ast(parse(query)) == print_ast(parse(expected))
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
                }
            }
        """),
    )
