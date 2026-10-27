.PHONY: run test lint docker

run:
	uv run finguard

test:
	./scripts/test.sh

lint:
	uv run ruff check src tests

docker:
	docker compose up --build
