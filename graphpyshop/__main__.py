from dotenv import load_dotenv, find_dotenv
from ariadne_codegen.main import client
from ariadne_codegen.config import get_config_dict


if __name__ == "__main__":
    load_dotenv(find_dotenv())
    client(get_config_dict())   