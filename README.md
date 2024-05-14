# A Python Shopify client that's always up to date

Tired of clients being not maintained? So are we. Which is why we built this.
Using codegen, so can easily stay up to date

# Development setup

- Rename .env.example to .env and fill in the values
- Currently also need to update the shopify store url in pyproject.toml

Run `ariadne-codegen` to generate the code

# Roadmap

## Organize and improve code

- Move utility queries separately from user queries
- Add basic tests
- Make jsonl reader handle nested connections
- Avoid src path in client
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

## Synchronization

- Add query generator with fragments to reduce duplication and get good defaults
- Respect and detect permissions given for the access token, allow and generate only valid queries

## Functionality

- Add easy oauth support / somehow via web or shopify cli?
- Allow using without needing a partner app
- Add pagination and regular querying for any level of nesting
- Make rate limit read max values and restore rate etc from responses
- Implement bulk mutations
- Improve checks for which queries are bulk query compatible and avoid generating bulk query versions and issue warnings for those that aren't
- Allow supporting multiple versions
- Allow supporting customer and storefront APIs in addition to admin
- Support & test multiple graphql queries
- Add the support to receive create, update, delete events over webhooks (ie just some fastapi instance, that can then easily be added to Modal as well)

## Performance

- Allow webhook notifications for checking job completion
- Store average requested query cost per each query and variable combinations and use those instead of the 1k max cost
- Use a single more performant json library like orjson or try to do it all via pydantic's faster built in parser
