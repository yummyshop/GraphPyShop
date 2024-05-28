import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import importlib

from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Union

import httpx
from ariadne_codegen.client_generators.dependencies.async_base_client import (
    AsyncBaseClient,
)
from httpx import (
    AsyncHTTPTransport,
    Request,
    Response,
)
from pydantic import BaseModel


class ThrottleStatus(BaseModel):
    maximumAvailable: int
    currentlyAvailable: int
    restoreRate: int


class QueryCost(BaseModel):
    requestedQueryCost: int
    actualQueryCost: int | None
    throttleStatus: ThrottleStatus


class BulkOperationsFinish(BaseModel):
    admin_graphql_api_id: str
    completed_at: Optional[datetime]
    created_at: datetime
    error_code: Optional[str]
    status: str
    type: str


def verify_webhook(data: bytes, hmac_header: str, secret: str) -> bool:
    digest = hmac.new(secret.encode("utf-8"), data, digestmod=hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest)

    return hmac.compare_digest(computed_hmac, hmac_header.encode("utf-8"))


def parse_query_name(query: str) -> str:
    match = re.search(r"query\s+(\w+)", query)
    return match.group(1) if match else "UnknownQuery"


class AsyncLimiterError(Exception):
    pass


class AsyncLimiterTransport(AsyncHTTPTransport):
    MAX_RETRIES = 10
    TOTAL_CAPACITY = 20000
    RESTORE_RATE = 1000  # points per 1000ms
    BACKOFF_FACTOR = 3

    def __init__(self):
        super().__init__()
        self.current_capacity = self.TOTAL_CAPACITY
        self.capacity_lock = asyncio.Lock()
        self.capacity_event = asyncio.Event()
        self.request_counter = 0

    async def _add_capacity_task(self):
        while True:
            await asyncio.sleep(0.01)  # 10ms
            await self.add_capacity(10)

    async def handle_async_request(self, request: Request) -> Response:
        if not hasattr(self, "_capacity_task"):
            self._capacity_task = asyncio.create_task(self._add_capacity_task())

        retries = self.MAX_RETRIES

        starting_query_cost = 1000
        requested_query_cost = starting_query_cost  # default cost
        self.request_counter += 1
        request_id = self.request_counter
        backoff_wait_time = 1

        while retries > 0:
            await self.acquire(requested_query_cost, request_id)
            response = await super().handle_async_request(request)
            cost = await self._get_query_cost(response, request_id)

            if cost:
                if cost.actualQueryCost is None:
                    # Set this more accurately now that we know the cost
                    requested_query_cost = cost.requestedQueryCost

                    # We got throttled, sync costs with server and retry
                    await self.sync_with_server(
                        cost.throttleStatus.currentlyAvailable, request_id
                    )
                    log_message = f"Request {request_id}: Retry {self.MAX_RETRIES - retries + 1} of {self.MAX_RETRIES} at cost {requested_query_cost} due to throttling. Current capacity: {self.current_capacity}"
                    if retries > self.MAX_RETRIES / 2:
                        logging.warning(log_message)
                    else:
                        logging.info(log_message)
                    retries -= 1

                    if retries < self.MAX_RETRIES - 1:
                        # Exponential backoff for retries after the first one
                        logging.info(
                            f"Request {request_id}: Waiting {backoff_wait_time} seconds before next retry due to exponential backoff."
                        )
                        await asyncio.sleep(backoff_wait_time)
                        backoff_wait_time *= self.BACKOFF_FACTOR

                    continue
                else:
                    # Return the unused cost to bucket, actually incurred cost needs to be recovered via restore rate over time
                    await self.add_capacity(
                        starting_query_cost - cost.actualQueryCost, request_id
                    )

            return response
        msg = f"Request {request_id}: Failed to handle request after {self.MAX_RETRIES} retries."
        raise AsyncLimiterError(msg)

    async def acquire(self, requested_query_cost: int, request_id: int):
        logging.debug(
            f"Request {request_id}: Attempting to ensure capacity for cost: {requested_query_cost}. Current capacity: {self.current_capacity}"
        )
        while True:
            async with self.capacity_lock:
                if self.current_capacity >= requested_query_cost:
                    logging.debug(
                        f"Request {request_id}: Current capacity ({self.current_capacity}) is sufficient for the requested cost ({requested_query_cost})."
                    )
                    self.current_capacity -= requested_query_cost
                    return True
                else:
                    logging.debug(
                        f"Request {request_id}: Current capacity ({self.current_capacity}) is not sufficient for the requested cost ({requested_query_cost}). Waiting for capacity to be restored."
                    )
            await (
                self.capacity_event.wait()
            )  # Wait until notified that capacity is restored
            self.capacity_event.clear()  # Clear the event after waking up

    async def add_capacity(self, amount_to_add: int, request_id: int | None = None):
        async with self.capacity_lock:
            if self.current_capacity < self.TOTAL_CAPACITY:
                self.current_capacity += min(
                    amount_to_add, self.TOTAL_CAPACITY - self.current_capacity
                )
                capacity_was_increased = True
            else:
                capacity_was_increased = False

        if capacity_was_increased:
            # Do this outside the lock
            logging.debug(
                f"Request {request_id}: Increased capacity by {amount_to_add} to {self.current_capacity}"
            )
            self.capacity_event.set()  # Notify waiting tasks that capacity has been restored

    async def sync_with_server(self, server_currently_available: int, request_id: int):
        async with self.capacity_lock:
            self.current_capacity = server_currently_available

        # Do this outside the lock
        logging.debug(
            f"Request {request_id}: Synced capacity with server to {self.current_capacity}"
        )
        self.capacity_event.set()  # Notify waiting tasks that capacity has been restored

    async def _get_query_cost(self, response: httpx.Response, request_id: int):
        try:
            await response.aread()
            response_json = response.json()
            throttle_status = QueryCost.model_validate(
                response_json.get("extensions", {}).get("cost", {})
            )
            return throttle_status
        except json.JSONDecodeError:
            return None


global_transport = AsyncLimiterTransport()


class ShopifyGetDataError(Exception):
    pass


class ShopifyAsyncBaseClient(AsyncBaseClient):
    BULK_QUERY_TRY_START_TIMEOUT = 600

    def __init__(
        self, url: str, access_token: str, transport: AsyncHTTPTransport | None = None
    ):
        if transport is None:
            transport = global_transport
        super().__init__(
            url=url,
            http_client=httpx.AsyncClient(
                transport=transport,
                headers={"X-Shopify-Access-Token": access_token},
                http2=True,
                http1=False,
                timeout=60,
            ),
        )

    def get_data(self, response: httpx.Response) -> Dict[str, Any]:
        data = super().get_data(response)

        # Find userErrors in any part of the response
        user_errors = None
        for value in data.values():
            if isinstance(value, dict) and "userErrors" in value:
                user_errors = value["userErrors"]
                break

        if user_errors:
            error_details = ", ".join(
                [
                    f"{', '.join(error.get('field') or ['Unknown field'])}: {error.get('message', 'Unknown error')}"
                    for error in user_errors
                ]
            )
            raise ShopifyGetDataError(error_details)

        return data

    def inject_variables(self, query: str, variables: dict[str, object]) -> str:
        # Remove variables and parentheses from the top level query
        query = re.sub(r"\(\$.*?\)", "", query)
        # Replace variables in the query
        for key, value in variables.items():
            if isinstance(value, str):
                query = query.replace(f"${key}", f'"{value}"')
            else:
                query = query.replace(f"${key}", str(value))

        return query

    async def try_create_bulk_query(
        self,
        bulk_operation_call: Callable[[str], Awaitable[Any]],
        gql_query: str,
        variables: Dict[str, object],
    ) -> Any:
        query_name = parse_query_name(gql_query)
        start_time = time.time()
        total_wait_time = 0
        while time.time() - start_time < self.BULK_QUERY_TRY_START_TIMEOUT:
            try:
                query = self.inject_variables(gql_query, variables)
                return await bulk_operation_call(query)
            except ShopifyGetDataError as e:
                current_time = time.time()
                elapsed_time = current_time - start_time
                if (
                    "A bulk query operation for this app and shop is already in progress"
                    in str(e)
                ):
                    formatted_time = time.strftime(
                        "%H:%M:%S.%f", time.gmtime(elapsed_time)
                    )
                    logging.info(
                        f"[{query_name}] Total wait time so far: {formatted_time}. Waiting due to job {e!s}."
                    )
                    await asyncio.sleep(2)
                    total_wait_time += 2
                else:
                    raise
        msg = f"[{query_name}] Timed out trying to create bulk query after waiting for {self.BULK_QUERY_TRY_START_TIMEOUT} seconds."
        raise TimeoutError(msg)

    def flatten_gql_response(
        self, json_data: Union[Dict[str, Any], List[Any], Any]
    ) -> Union[List[Any], Dict[str, Any], Any]:
        if isinstance(json_data, dict):
            if "edges" in json_data:
                # Extract the list of nodes from the edges
                return [
                    self.flatten_gql_response(item["node"])
                    for item in json_data["edges"]
                ]
            else:
                # Recursively apply flattening to each dictionary entry
                return {
                    key: self.flatten_gql_response(value)
                    for key, value in json_data.items()
                }
        elif isinstance(json_data, list):
            # Apply flattening to each item in the list
            return [self.flatten_gql_response(item) for item in json_data]
        else:
            # Return the item itself if it's neither a dict nor a list
            return json_data

    async def run_bulk_operation(
        self,
        operation_callable: Callable[[str], Awaitable[Any]],
        check_callable: Callable[[str], Awaitable[Any]],
        gql_query: str,
        variables: Dict[str, Any],
        bulk_operation_status: Any,
        bulk_operation_node_bulk_operation: Any,
        typename_to_class_map: Dict[str, str]
    ) -> AsyncGenerator[Any, None]:
        query_name = parse_query_name(gql_query)
        response = await self.try_create_bulk_query(
            operation_callable, gql_query, variables
        )
        if response and response.bulk_operation:
            status = response.bulk_operation.status
            while status in [
                bulk_operation_status.CREATED,
                bulk_operation_status.RUNNING,
                bulk_operation_status.CANCELING,
            ]:
                await asyncio.sleep(2)  # Wait before checking the status again
                status_response = await check_callable(response.bulk_operation.id)
                current_time = datetime.now(timezone.utc)
                created_at_datetime = datetime.fromisoformat(
                    status_response.created_at.replace("Z", "+00:00")
                )
                running_duration = current_time - created_at_datetime
                object_count = float(
                    status_response.object_count
                )  # Ensure object_count is a float
                elapsed_seconds = running_duration.total_seconds()

                if elapsed_seconds > 0:
                    objects_per_second = round(object_count / elapsed_seconds, 2)
                else:
                    objects_per_second = 0

                logging.info(
                    f"[{query_name}] Job: {status_response.id}, Status: {status_response.status.value}, Runtime: {running_duration}, Objects: {status_response.object_count}, Rate: {objects_per_second}/s"
                )
                if status_response and isinstance(
                    status_response, bulk_operation_node_bulk_operation
                ):
                    status = status_response.status
                    if (
                        status == bulk_operation_status.COMPLETED
                        and status_response.url
                    ):
                        logging.info(
                            f"[{query_name}] Retrieving JSONL file for job {status_response.id}"
                        )
                        async for item in self.get_jsonl(
                            status_response.url,
                            typename_to_class_map
                        ):
                            yield item

    # TODO: Refactor this to automatically detect types from jsonl and stitch connections together
    async def get_jsonl(self, url: str, typename_to_class_map: Dict[str, str]):
        parent_objects: dict[str, Any] = {}
        last_parent_id = None
        missing_fields: dict[str, str] = {}

        async with httpx.AsyncClient() as client, client.stream("GET", url) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                parsed_line = json.loads(line)
                if "__parentId" in parsed_line:
                    parent_id = parsed_line.pop("__parentId")  # Remove __parentId from the record
                    logging.debug(f"Found child with parent ID: {parent_id}")
                    if parent_id in parent_objects:
                        parent = parent_objects[parent_id]
                        logging.debug(f"Parent found for ID {parent_id}")
                        parent_field = missing_fields.get(parsed_line["__typename"])
                        if parent_field:
                            logging.debug(f"Type match found for {parsed_line['__typename']} under attribute {parent_field}")
                            parent[parent_field]["edges"].append({"node": parsed_line})
                            logging.debug(f"Child appended to parent under {parent_field}.")
                        else:
                            logging.warning(f"No matching field found for {parsed_line['__typename']} in missing_fields.")
                else:
                    if last_parent_id is not None and last_parent_id in parent_objects:
                        class_name = typename_to_class_map[parent_objects[last_parent_id]["__typename"]]
                        module = importlib.import_module("graphpyshop.client")
                        class_ = getattr(module, class_name)
                        yield class_.model_validate(parent_objects[last_parent_id])
                        del parent_objects[last_parent_id]  # Free memory

                    new_parent = parsed_line.copy()
                    for field_name, field in return_type.__fields__.items():
                        field_key = field.alias or field_name
                        if field_key not in new_parent:
                            new_parent[field_key] = {"edges": []}
                            node_class = field.annotation.__fields__["edges"].annotation.__args__[0].__fields__["node"].annotation
                            node_class_literal = node_class.schema()["properties"]["__typename"]["const"]
                            logging.debug(f"Node class for field '{field_key}': {node_class_literal}")
                            if node_class_literal:
                                missing_fields[node_class_literal] = field_key

                    parent_objects[parsed_line["id"]] = new_parent
                    last_parent_id = parsed_line["id"]

        if last_parent_id is not None and last_parent_id in parent_objects:
            class_name = typename_to_class_map[parent_objects[last_parent_id]["__typename"]]
            module = importlib.import_module("graphpyshop.client")
            class_ = getattr(module, class_name)
            yield class_.model_validate(parent_objects[last_parent_id])
            logging.debug(f"Missing fields: {missing_fields}")
