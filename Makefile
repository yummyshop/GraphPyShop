SHELL := /bin/bash
python_version = 3.10.13
venv_prefix = pyshop
venv_name = $(venv_prefix)-$(python_version)
pyenv_instructions=https://github.com/pyenv/pyenv#installation



init: require_pyenv  ## Setup a dev environment for local development.
	@pyenv install $(python_version) -s
	@echo -e "\033[0;32m ✔️  🐍 $(python_version) installed \033[0m"
	@if ! [ -d "$$(pyenv root)/versions/$(venv_name)" ]; then \
		pyenv virtualenv $(python_version) $(venv_name); \
	fi
	@pyenv local $(venv_name)
	@echo -e "\033[0;32m ✔️  🐍 $(venv_name) virtualenv activated \033[0m"

	@echo -e "\nEnvironment setup! ✨ 🍰 ✨ 🐍 \n\nCopy this path to tell PyCharm where your virtualenv is. You may have to click the refresh button in the PyCharm file explorer.\n"
	@echo -e "\033[0;32m$$(pyenv which python)\033[0m\n"
	@echo -e "The following commands are available to run in the Makefile:\n"
	@make -s help

deps: dependencies
dependencies:  ## Install the dependencies
	@export VIRTUAL_ENV=$$(pyenv prefix); \
	if uv --help >/dev/null 2>&1; then \
		echo -e "\033[0;32m ✔️  uv detected \033[0m"; \
		uv pip install --upgrade uv; \
	else \
		echo -e "\033[0;31m ✖️  uv not detected or non-functional, installing uv \033[0m"; \
		pip install --upgrade pip uv; \
	fi; \
	uv pip sync tests/requirements-dev.txt; \
	uv pip install -e .

codegen:  ## Generate code from the queries
	@ariadne-codegen

build:  ## Build the package
	@python -m build

requirements:  ## Freeze the requirements.txt file
	uv pip compile pyproject.toml tests/requirements-dev.in --output-file=tests/requirements-dev.txt --upgrade

af: autoformat  ## Alias for `autoformat`
autoformat:  ## Run the autoformatter.
	@-ruff check --config tests/ruff.toml . --fix-only
	@ruff format --config tests/ruff.toml .

lint:  ## Run the code linter.
	@ruff check --config tests/ruff.toml .
	@echo -e "No linting errors - well done! ✨ 🍰 ✨"

type-check: ## Run the type checker.
	@mypy --config-file tox.ini .

require_pyenv:
	@if ! [ -x "$$(command -v pyenv)" ]; then\
	  echo -e '\n\033[0;31m ❌ pyenv is not installed.  Follow instructions here: $(pyenv_instructions)\n\033[0m';\
	  exit 1;\
	else\
	  echo -e "\033[0;32m ✔️  pyenv installed\033[0m";\
	fi

help: ## Show this help message.
	@## https://gist.github.com/prwhite/8168133#gistcomment-1716694
	@echo -e "$$(grep -hE '^\S+:.*##' $(MAKEFILE_LIST) | sed -e 's/:.*##\s*/:/' -e 's/^\(.\+\):\(.*\)/\\x1b[36m\1\\x1b[m:\2/' | column -c2 -t -s :)" | sort