.PHONY: sync format lint shellcheck actionlint test bats image smoke helper-test helper-container

# Pinned, and run through uvx, so CI and a laptop run the same version with
# nothing to install by hand.
SHELLCHECK_VERSION := 0.11.0.1
ACTIONLINT_VERSION := 1.7.12.25
BATS_VERSION := 1.13.0
SHELL_SOURCES := $(wildcard utils/*.sh) tests/bats/stubs/gh
MKVER := .tools/bin/git-mkver

# Which backend `make image` and `make smoke` build; see the registry.
BACKEND ?= core
# docker, or Apple's container.
RUNTIME ?= docker

sync:
	uv sync

format: sync
	uv run black .
	uv run ruff check --fix .
	cd helper && uv run black . && uv run ruff check --fix .

lint: sync shellcheck actionlint
	uv run ruff check .
	uv run black --check .
	cd helper && uv run ruff check . && uv run black --check .

shellcheck:
	uvx --from shellcheck-py==$(SHELLCHECK_VERSION) shellcheck -x $(SHELL_SOURCES)

actionlint:
	uvx --from actionlint-py==$(ACTIONLINT_VERSION) actionlint

test: sync
	uv run pytest -v

# The release scripts' tests, run with the same pinned git-mkver CI uses.
bats: $(MKVER)
	npx --yes bats@$(BATS_VERSION) tests/bats

$(MKVER):
	INSTALL_DIR=.tools/bin utils/install_git_mkver.sh

# The test helper is its own project on Python 3.11 (ADR 0003). Its tests run
# the server from this checkout in process mode. HELPER_PYTHON picks another
# interpreter, as CI does to test 3.14 as well.
HELPER_PYTHON ?= 3.11

helper-test:
	cd helper && uv run --python $(HELPER_PYTHON) pytest -v

# The helper against a real core image, under docker or Apple's container.
helper-container:
	$(MAKE) image BACKEND=core
	cd helper && FAKE_BACKENDS_TEST_IMAGE=fake-core:dev uv run --python $(HELPER_PYTHON) pytest -v tests/test_container.py

image:
	$(RUNTIME) build --build-arg BACKEND=$(BACKEND) -t fake-$(BACKEND):dev .

smoke: image
	RUNTIME=$(RUNTIME) utils/smoke_test_image.sh fake-$(BACKEND):dev
