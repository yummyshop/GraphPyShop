import argparse
import logging
import os
import shutil

from ariadne_codegen.config import get_client_settings, get_config_dict
from dotenv import find_dotenv, load_dotenv

logging.basicConfig(level=logging.INFO)


def generate_client():
    from ariadne_codegen.main import client

    logging.info("Starting generation of client")

    load_dotenv(find_dotenv())
    client(get_config_dict())


def generate_queries():
    from graphpyshop.extensions.shopify_generate_queries import ShopifyQueryGenerator

    load_dotenv(find_dotenv())

    config_dict = get_config_dict()
    settings = get_client_settings(config_dict)

    schema_path = f"{settings.target_package_path}/schema.graphql"

    logging.info(f"Looking for schema at {schema_path}")

    if os.path.exists(schema_path):
        logging.info(f"Schema found at {schema_path}")
        settings.schema_path = schema_path
    else:
        logging.info("Schema not found, will write schema to file")
        # settings.write_schema_to_file = True

    query_generator = ShopifyQueryGenerator(settings)
    query_generator.generate_queries()


def clean():
    settings = get_client_settings(get_config_dict())

    paths = [
        f"{settings.target_package_path}/schema.graphql",
        f"{settings.target_package_path}/{settings.target_package_name}",
        f"{settings.queries_path}/lists",
        f"{settings.queries_path}/objects",
    ]
    for path in paths:
        if os.path.exists(path):
            if os.path.isfile(path):
                os.remove(path)
            else:
                shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description="GraphPyShop CLI")
    parser.add_argument(
        "command",
        choices=["generate-client", "generate-queries", "clean"],
        help="Command to run",
    )
    args = parser.parse_args()

    if args.command == "generate-client":
        generate_client()
    elif args.command == "generate-queries":
        generate_queries()
    elif args.command == "clean":
        clean()


if __name__ == "__main__":
    main()
