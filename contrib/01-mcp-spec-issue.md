# Draft: MCP spec issue — per-error retry semantics for tool results

> **Before filing:** search open issues/SEPs in
> [`modelcontextprotocol/modelcontextprotocol`](https://github.com/modelcontextprotocol/modelcontextprotocol)
> for "retry", "idempotent" and "error" so you link (not duplicate) prior discussion. Attach the
> toolproof numbers from your own run (`toolproof eval --unsafe-retry-writes`) as evidence.

---

**Title:** Tool errors should say whether retrying is safe (`retryable`, `retryAfterMs`, `outcome`)

## Problem

`ToolAnnotations.idempotentHint` tells a client whether a tool is *in general* safe to call twice.
It says nothing about *a specific failure*. When `tools/call` returns `isError: true`, a client or
model cannot tell apart:

1. a failure where nothing happened (validation error, 429 before processing): retry is safe;
2. a failure where the side effect **did** happen (upstream 5xx/timeout *after* the write): retry
   duplicates the effect;
3. a failure where the server cannot know which of the two happened.

Today every server invents its own error text, and models (or clients) guess, often by simply retrying.

## Evidence

Using [toolproof](https://github.com/SCVaitla/toolproof), a Slack MCP server evaluated against a
deterministic Slack digital twin with injected faults (429s, HTML 5xx, and "write applied, then 500"):

| Server behaviour on ambiguous write | Stress pass rate | Duplicate posts (30 chaos write trials) |
|---|---:|---:|
| Blind retry (common default) | 63% | 12 |
| Report outcome as unknown + verify-then-retry | 100% | 0 |

Same agent (a scripted reference agent, so the model is held constant), same 6 write tasks, 5 seeded
trials each: the only variable is whether the retry decision had the information it needed.
Reproduce with `toolproof eval --models mock:golden --only write --trials 5 [--unsafe-retry-writes]`.

## Proposal

Add optional, namespaced fields to `CallToolResult` when `isError` is true (via `_meta` first, so
it's backward compatible, and promoted to first-class fields if adopted):

```json
{
  "isError": true,
  "content": [{"type": "text", "text": "chat.postMessage failed ambiguously (HTTP 500)"}],
  "_meta": {
    "io.modelcontextprotocol/retry": {
      "retryable": false,
      "retryAfterMs": null,
      "outcome": "unknown"
    }
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `retryable` | boolean | Re-issuing the identical call is safe and may succeed. |
| `retryAfterMs` | integer \| null | Minimum wait before retrying (e.g. from upstream `Retry-After`). |
| `outcome` | `"not_applied"` \| `"applied"` \| `"unknown"` | Whether the call's side effect took place. Read-only tools always use `"not_applied"`. |

Guidance for clients: if `outcome` is `"unknown"`, a client **should not** auto-retry and **should**
surface the ambiguity to the model or user (or call a read tool to verify state).

## Alternatives considered

- **Idempotency keys** on every write: the right long-term answer for APIs that support them, but
  most upstream APIs MCP servers wrap (Slack, Gmail, Jira) don't, so servers still need a way to
  report ambiguity.
- **Free-text conventions** in `content`: not machine-readable, inconsistent across servers.
- **JSON-RPC error codes**: tool execution errors are deliberately returned as results, not
  protocol errors, so this belongs on `CallToolResult`.

## Open questions

1. `_meta` namespace first, or straight to first-class optional fields?
2. Should `retryable: true` with a non-idempotent tool be a spec-level contradiction?
3. Should the TS/Python SDKs expose a `RetryableToolError` helper (as some frameworks already do)?

I'm happy to open a SEP PR and prototype the Python SDK helper.
