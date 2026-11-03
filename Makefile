.PHONY: install test lint fmt eval eval-unsafe gate lint-tools serve-twin all

install:
	pip install -e ".[dev]"

test:
	pytest
	cd gate && go test ./...

lint:
	ruff check src tests
	ruff format --check src tests
	cd gate && test -z "$$(gofmt -l .)" && go vet ./...

fmt:
	ruff format src tests
	ruff check --fix src tests
	cd gate && gofmt -w .

eval:
	toolproof eval --models mock:golden,mock:flawed --conditions clean,chaos,storm --trials 3 --out reports/latest

eval-unsafe:  ## same suite, but the server blindly retries ambiguous writes: watch duplicates appear
	toolproof eval --models mock:golden --only write --trials 5 --unsafe-retry-writes --out reports/unsafe

lint-tools:
	toolproof export-tools --out tools.json
	cd gate && go run ./cmd/toolproof-gate lint --strict ../tools.json

gate: eval
	cd gate && go run ./cmd/toolproof-gate check --results ../reports/latest/results.json --model golden \
		--min-composite 0.95 --min-pass-rate 1.0 --max-blast-radius 0 --max-duplicates 0

serve-twin:
	toolproof serve --twin

all: lint test lint-tools gate
