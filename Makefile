.PHONY: sync format lint test

sync:
	uv sync

format: sync
	uv run black .
	uv run ruff check --fix .

lint: sync
	uv run ruff check .
	uv run black --check .

test: sync
	uv run pytest -v
