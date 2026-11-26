.PHONY: sync format lint shellcheck actionlint test bats image smoke

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

lint: sync shellcheck actionlint
	uv run ruff check .
	uv run black --check .

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

image:
	$(RUNTIME) build --build-arg BACKEND=$(BACKEND) -t fake-$(BACKEND):dev .

smoke: image
	RUNTIME=$(RUNTIME) utils/smoke_test_image.sh fake-$(BACKEND):dev
