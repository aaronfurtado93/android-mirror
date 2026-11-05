# Android Mirror: developer tasks. Run `make help` for a list.
PYTHON  ?= python3
VENV    := .venv
BIN     := $(VENV)/bin
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' src/android_mirror/__init__.py)
BUNDLE  := android-mirror-$(VERSION)

.PHONY: help dev run test lint format check build dist install uninstall clean

help:  ## show this help
	@awk -F':.*## ' '/^[a-z-]+:.*## / {printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(BIN)/android-mirror: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install -q --upgrade pip
	$(BIN)/python -m pip install -q -e '.[dev]'
	@touch $@

dev: $(BIN)/android-mirror  ## create .venv with an editable install + dev tools

run: dev  ## run the server from source (foreground, opens the browser)
	$(BIN)/android-mirror serve

test: dev  ## run the test suite
	$(BIN)/pytest

lint: dev  ## static checks (ruff + JS syntax)
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests
	@command -v node >/dev/null && node --check src/android_mirror/web/app.js || true
	bash -n install.sh start.sh

format: dev  ## auto-format Python sources
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

check: lint test  ## lint + test

build: dev  ## build the wheel and sdist into dist/
	rm -rf dist build
	$(BIN)/python -m build

dist: build  ## release bundle: dist/android-mirror-<version>.tar.gz (wheel + installer)
	rm -rf dist/$(BUNDLE)
	mkdir -p dist/$(BUNDLE)
	cp dist/*.whl install.sh README.md LICENSE dist/$(BUNDLE)/
	tar -C dist -czf dist/$(BUNDLE).tar.gz $(BUNDLE)
	rm -rf dist/$(BUNDLE)
	@echo "Release bundle: dist/$(BUNDLE).tar.gz  (unpack and run ./install.sh)"

install:  ## install for the current user (same as ./install.sh)
	./install.sh

uninstall:  ## remove the installed app (keeps remembered devices)
	./install.sh --uninstall

clean:  ## remove build artefacts
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
