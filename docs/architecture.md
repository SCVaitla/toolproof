# Architecture and design decisions

## Layers

```
model adapter ─► agent loop ─► MCP ClientSession ─► MCP server (FastMCP) ─► SlackClient ─► httpx transport
                                                                                              ├─ real: slack.com
                                                                                              └─ eval: SlackTwin (+ChaosConfig)
```

Everything below the agent loop is production code. The only thing an eval swaps is the httpx
transport, plus a virtual clock and seeded RNG for determinism.

## SlackClient

* **One call path.** `call()` acquires a rate-limit token, sends, classifies the response into a typed
  error (or success), and decides whether to retry. Classification is explicit about *where* a
  failure happened:
  * `ConnectError`: the request never left, so retry is safe even for writes;
  * 429 / `ok:false ratelimited`: Slack refused before processing; retry after `Retry-After`;
  * 5xx, timeouts, non-JSON bodies, `internal_error`: **ambiguous**. Safe to retry for reads and
    idempotent writes; for non-idempotent writes (`chat.postMessage`) it raises
    `OutcomeUnknownError` instead.
* **Rate limiting.** A token bucket per method, sized from Slack's tier for that method. A 429
  *penalizes* the bucket (`blocked_until`), so concurrent callers stop together instead of each
  discovering the limit separately.
* **Retry delay** = `max(full_jitter(exponential_backoff), Retry-After)`.
* **Pagination.** `collect(max_items=N)` shrinks the final page's `limit` to what's still needed, so
  the cursor it returns never skips items. Tools expose `next_cursor` only when truncated.
* **Auth.** `TokenProvider` protocol: static bot token, or `OAuthTokenProvider` which refreshes
  proactively (5 min skew) and reactively on `token_expired`, double-checked under a lock so N
  concurrent calls trigger one refresh. Token files are written atomically with mode 0600.

## MCP server

* Models say `#releases`, not `C0123ABC`, so tools resolve names (paginating `conversations.list`)
  and cache them. Unknown names return `did_you_mean`.
* **Verify-then-retry** for `slack_send_message`: on `OutcomeUnknownError`, read recent history (or
  the thread, normalized to its root) for a message with the bot's user ID and identical text. Found:
  report success (`recovered: true`). Not found: re-post (bounded to 3 attempts).
* Errors are JSON with `retryable`, `retry_after_seconds` and a `hint`, so the model knows what to do.

## Twin

* `World` holds users, channels, messages and a **mutation journal** (`message_posted`,
  `reaction_added`). Fixtures are YAML with generators for noise (channels, users, filler messages).
* `SlackTwin` serves the Web API methods the server uses, with Slack-like semantics that matter for
  agents: newest-first history, parent-included replies, private-channel invisibility,
  `not_in_channel`, `is_archived`, reply-to-reply landing in the root thread, `already_reacted`,
  `missing_scope` with `needed`, and OAuth exchange/refresh with rotation.
* `ChaosConfig` decides faults per request from a seeded RNG. Trial seeds are
  `crc32(case|condition|trial)`, so a failing trial reproduces exactly.

## Grading

Outcome is computed from the journal: each expected post/reaction consumes one matching mutation;
whatever remains is the blast radius, and remaining posts whose (channel, text) duplicate another
are counted as duplicates. Tool-call critics run separately and only affect `selection`/`args`, so
a valid alternative path can pass while still being scored on argument quality.

A trial **passes** iff outcome = 1.0 and unexpected mutations ≤ `max_unexpected`. Provider failures
are recorded as infra errors and excluded from rates.

## Determinism

* Scripted agents + seeded chaos + virtual clock: the same command produces the same results.json
  (apart from timestamps and wall time).
* Real models add their own nondeterminism; that is what `--trials` and pass^k measure.

## Known limitations

* The twin implements the subset of Slack the tools use; it is not a general Slack emulator.
* Verify-then-retry is a best-effort duplicate check: in a real outage a message could land after
  the verification read. The bounded retry and the `outcome_unknown` error make that residual risk
  explicit rather than hidden.
* Text-based checks (`contains_any`) for refusal answers are heuristics; an LLM-judge critic would
  be a natural extension.
