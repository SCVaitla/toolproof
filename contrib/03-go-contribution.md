# Draft: a small Go contribution

This repo already ships Go: [`gate/`](../gate) is a dependency-free CI gate and MCP tool-schema
linter with tests (`go test ./...`). Use it as your Go writing sample, then pick one upstream target:

1. **Arcade's Go code, if public.** Look for Go repositories under
   [github.com/ArcadeAI](https://github.com/ArcadeAI). Good first PRs: a failing-test-first bug fix,
   table-driven tests for an under-tested package, or clearer error messages. Read open issues first.

2. **The official MCP Go SDK**, [`modelcontextprotocol/go-sdk`](https://github.com/modelcontextprotocol/go-sdk).
   Candidate ideas (check issues for interest before building):
   - an example under `examples/` that validates `ToolAnnotations` consistency (e.g. a write tool
     without `idempotentHint`), ported from `gate/internal/lint`;
   - tests or docs around tool error results and retries, tied to the spec issue in
     [`01-mcp-spec-issue.md`](01-mcp-spec-issue.md).

3. **Upstream the linter rules** as a standalone `mcp-lint` Go module others can `go install`.

Whatever you choose: keep it small, include tests, run `gofmt`/`go vet`, and link the issue.
