import argparse
import ast
import functools
import logging.config
import os
import shutil
import time
from functools import lru_cache

from ariadne_codegen.config import get_client_settings, get_config_dict
from dotenv import find_dotenv, load_dotenv

logging.basicConfig(level=logging.INFO)


class TimedLog:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.start = time.time()

    def __exit__(self, *args):
        duration = (time.time() - self.start) * 1000
        logging.info(f"{self.name} took {duration:.1f} ms")


def _simple_ast_to_str(
    ast_obj: ast.AST,
    remove_unused_imports: bool = True,
    multiline_strings: bool = False,
    multiline_strings_offset: int = 4,
) -> str:
    """
    Convert ast object into string.

    Doesn't do expensive autoformatting like default does
    """
    return ast.unparse(ast_obj)


@lru_cache
def monkeypatch_ariadne_codegen():
    from ariadne_codegen.client_generators import package

    package.ast_to_str = _simple_ast_to_str


@lru_cache
def monkey_patch_httpx():
    """Wrap all the httpx functions to have a default timeout of 30 (get, post, etc)"""
    import httpx
    from httpx import Timeout

    new_timeout = Timeout(90)

    original_get = httpx.get

    @functools.wraps(httpx.get)
    def get(*args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = new_timeout
        return original_get(*args, **kwargs)

    httpx.get = get

    original_post = httpx.post

    @functools.wraps(httpx.post)
    def post(*args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = new_timeout
        return original_post(*args, **kwargs)

    httpx.post = post


def generate_client():
    from ariadne_codegen.main import client

    logging.info("Starting generation of client")

    load_dotenv(find_dotenv())
    config_dict = get_config_dict()
    with TimedLog("Client generation"):
        client(config_dict)


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


def configure_logging():
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"}
        },
        "handlers": {
            "default": {
                "level": "INFO",
                "formatter": "standard",
                "class": "logging.StreamHandler",
            }
        },
        "loggers": {"": {"handlers": ["default"], "level": "INFO", "propagate": True}},
    }
    logging.config.dictConfig(log_config)


def main():
    parser = argparse.ArgumentParser(description="GraphPyShop CLI")
    parser.add_argument(
        "command",
        choices=["generate-client", "generate-queries", "clean"],
        help="Command to run",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable profiling",
    )
    args = parser.parse_args()
    configure_logging()
    monkey_patch_httpx()
    monkeypatch_ariadne_codegen()
    from graphpyshop.profiler import CallGrindProfiler

    with CallGrindProfiler(f"Command {args.command}", enabled=args.profile):
        if args.command == "generate-client":
            generate_client()
        elif args.command == "generate-queries":
            generate_queries()
        elif args.command == "clean":
            clean()


if __name__ == "__main__":
    main()
