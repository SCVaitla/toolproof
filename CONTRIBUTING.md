# Contributing

```bash
pip install -e ".[dev]"
make lint test   # ruff, pytest, gofmt, go vet, go test
make eval        # offline evals with the scripted agents
```

* **New eval case:** add it to `evals/cases/*.yaml` with a `golden` trajectory. `mock:golden` must
  score 1.0 in `clean` and `chaos`; CI enforces this, which keeps the grader honest.
* **New tool:** add it in `src/toolproof/server/tools.py` with honest annotations and described
  parameters, implement any new Slack methods in the twin, and run `make lint-tools`.
* **New fault:** add it to `ChaosConfig` and the twin's dispatcher, with a test showing the client
  or server handles it.

Please keep PRs small and tested.
