from dotenv import load_dotenv, find_dotenv
from ariadne_codegen.main import client
from ariadne_codegen.config import get_config_dict
from .extensions.shopify_generate_queries import shopify_generate_queries
import logging 
import argparse

logging.basicConfig(level=logging.INFO)

def generate_client():
    logging.info("Starting generation of client")

    load_dotenv(find_dotenv())
    client(get_config_dict())

def generate_queries():
    load_dotenv(find_dotenv())
    shopify_generate_queries(get_config_dict())

def main():
    parser = argparse.ArgumentParser(description="GraphPyShop CLI")
    parser.add_argument("command", choices=["generate-client", "generate-queries"], help="Command to run")
    args = parser.parse_args()

    if args.command == "generate-client":
        generate_client()
    elif args.command == "generate-queries":
        generate_queries()

if __name__ == "__main__":
    main()