from dotenv import load_dotenv, find_dotenv
from ariadne_codegen.config import get_config_dict, get_client_settings
import logging
import argparse
import os
import shutil

logging.basicConfig(level=logging.INFO)


def generate_client():
    from ariadne_codegen.main import client

    logging.info("Starting generation of client")

    load_dotenv(find_dotenv())
    client(get_config_dict())


def generate_queries():
    from .extensions.shopify_generate_queries import shopify_generate_queries

    load_dotenv(find_dotenv())
    shopify_generate_queries(get_config_dict())


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
