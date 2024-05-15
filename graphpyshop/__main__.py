import os
from dotenv import load_dotenv, find_dotenv
from ariadne_codegen.main import client
from ariadne_codegen.config import get_config_dict
from .extensions.shopify_generate_queries import generate_queries
import logging 

logging.basicConfig(level=logging.INFO)

def generate_client():
    logging.info("Starting generation of client")

    load_dotenv(find_dotenv())
    client(get_config_dict())

def generate_queries():
    logging.info("Starting generation of queries")

    load_dotenv(find_dotenv())
    generate_queries(get_config_dict())