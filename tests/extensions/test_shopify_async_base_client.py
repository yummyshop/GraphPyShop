import pytest
import httpx
from shopify_client.src.client.shopify_async_base_client import (
    ShopifyAsyncBaseClient,
    verify_webhook,
    BulkOperationsFinish,
)
from unittest.mock import AsyncMock, patch


@pytest.fixture
def client():
    return ShopifyAsyncBaseClient(url="https://example.com", access_token="dummy_token")


@pytest.mark.asyncio
async def test_get_data(client):
    response = httpx.Response(200, json={"data": {"key": "value"}})
    data = client.get_data(response)
    assert data == {"key": "value"}


@pytest.mark.asyncio
async def test_get_data_with_user_errors(client):
    response = httpx.Response(
        200,
        json={
            "data": {"userErrors": [{"field": ["field1"], "message": "error message"}]}
        },
    )
    with pytest.raises(Exception, match="User errors occurred: field1: error message"):
        client.get_data(response)


def test_inject_variables(client):
    query = "query ($var1: String) { field1 }"
    variables = {"var1": "value1"}
    result = client.inject_variables(query, variables)
    assert result == "query  { field1 }"


@pytest.mark.asyncio
async def test_try_create_bulk_query(client):
    bulk_operation_call = AsyncMock()
    bulk_operation_call.return_value = {"bulk_operation": {"status": "COMPLETED"}}
    gql_query = "query { field }"
    variables = {"var1": "value1"}

    result = await client.try_create_bulk_query(
        bulk_operation_call, gql_query, variables
    )
    assert result == {"bulk_operation": {"status": "COMPLETED"}}


def test_verify_webhook():
    data = b"test data"
    secret = "secret"
    hmac_header = "computed_hmac"
    with patch("hmac.compare_digest", return_value=True):
        assert verify_webhook(data, hmac_header, secret)


@pytest.mark.asyncio
async def test_run_bulk_operation(client):
    operation_callable = AsyncMock()
    check_callable = AsyncMock()
    gql_query = "query { field }"
    variables = {"var1": "value1"}
    bulk_operation_status = AsyncMock()
    bulk_operation_node_bulk_operation = AsyncMock()
    return_type = AsyncMock()

    operation_callable.return_value = {
        "bulk_operation": {
            "status": "COMPLETED",
            "id": "1",
            "url": "http://example.com",
        }
    }
    check_callable.return_value = {
        "status": "COMPLETED",
        "id": "1",
        "url": "http://example.com",
    }

    async for item in client.run_bulk_operation(
        operation_callable,
        check_callable,
        gql_query,
        variables,
        bulk_operation_status,
        bulk_operation_node_bulk_operation,
        return_type,
    ):
        assert item is not None


@pytest.mark.asyncio
async def test_get_jsonl(client):
    url = "http://example.com"
    return_type = AsyncMock()
    return_type.model_validate = lambda x: x

    with patch("httpx.AsyncClient.stream", AsyncMock()) as mock_stream:
        mock_stream.return_value.__aenter__.return_value.aiter_lines = AsyncMock(
            return_value=iter([json.dumps({"id": "1", "__typename": "Type"})])
        )

        async for item in client.get_jsonl(url, return_type):
            assert item == {"id": "1", "__typename": "Type"}
