.PHONY: install lint test validate oracle bench serve docker

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

test: lint
	pytest -q

validate:
	python -m codeverify.eval.cli --validate-only

oracle:
	python -m codeverify.eval.cli --oracle --out benchmarks/results --tag oracle-selftest

bench:
	python -m codeverify.eval.cli --resume --out benchmarks/results

serve:
	python -m codeverify.api.app

docker:
	docker compose up --build
