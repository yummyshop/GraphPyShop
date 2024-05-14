# A Python Shopify client that's always up to date

Tired of clients being not maintained? So are we. Which is why we built this.
Using codegen against Shopify schema and a set of graphql queries, so you can stay up to date easily.

# Key features
- Fully async client for high performance
- Bulk query support built in for all queries, just prepend bq_ to any query
- Shopify graphql query cost based rate limit handling for high performance
- Fully typed

# Usage example
```python
from graphpyshop.client import ShopifyClient
import asyncio
import logging

logging.basicConfig(level=logging.INFO) # Show logs for client
logging.getLogger("httpx").setLevel(logging.WARNING) # Info gets excessive

shopify_store = 'example-store'
shopify_version = 'unstable'
shopify_access_token = 'shpa_1234567890'

client = graphpyshop.ShopifyClient(
    url=f"https://{shopify_store}.myshopify.com/admin/api/{shopify_version}/graphql.json",
    access_token=shopify_access_token,
)

async def fetch_products():
    products = await client.products(first=100, query="")
    print(products)

    # We automatically inject input variables for bulk query queries
    products_bulk = await client.bq_products(first=100, query="") 
    print(products_bulk)

# To run the async function
asyncio.run(fetch_products())
```

# Development setup

- Rename .env.example to .env and fill in the values
- Currently also need to update the shopify store url in pyproject.toml

Modify and the required graphql queries under `graphpyshop/queries`
Run `ariadne-codegen` to generate the corresponding client

# Roadmap

## Organize and improve code

- Move utility queries separately from user queries
- Add basic tests
- Make jsonl reader handle nested connections
- Move flatten to a base model
- Fix the package and plugin references without needing to install it first
- Clean up the bulk query plugin
- Improve naming of generated classes so there's no SubscriptionContractsSubscriptionContractsEdgesNode
- Improve typing in the base client
- Get rid of ariadne-codegen dependency on the generated client
- Update variable injection to be more robust (ie AST based)

## Documentation

- Add basic readme
- Add basic examples
- Add examples on how to use with modal, webhooks, dlt
- Add full documentation site

## ETL Functionality

- Add query generator with fragments to reduce duplication and get good defaults
- Respect and detect permissions given for the access token, allow and generate only valid queries

## Functionality

- Add easy oauth support / somehow via web or shopify cli?
- Allow using without needing a partner app
- Add pagination and regular querying for any level of nesting
- Make rate limit read max values and restore rate etc from responses
- Implement bulk mutations
- Improve checks for which queries are bulk query compatible and avoid generating bulk query versions and issue warnings for those that aren't
- Support multiple api versions
- Support customer and storefront APIs in addition to admin
- Support & test multiple graphql queries
- Add the support to receive create, update, delete events over webhooks (ie just some fastapi instance, that can then easily be added to Modal as well)
- Auto handle defaults for input variables, ie first could be filled up to be 250 which is the shopify max
- Use TQDM or allow generic progress bar implementation for tracking progress

## Performance

- Allow webhook notifications for checking job completion
- Store average requested query cost per each query and variable combinations and use those instead of the 1k max cost
- Use a single more performant json library like orjson or try to do it all via pydantic's faster built in parser
- Queue for bulk jobs
- Add benchmarks for deserializing JSONL and other common tasks
