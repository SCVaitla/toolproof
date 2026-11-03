# Draft: contribution to Arcade's open-source toolkits

Target repo: [`ArcadeAI/arcade-mcp`](https://github.com/ArcadeAI/arcade-mcp) (MCP framework + toolkits).

> **Before opening anything:** clone the repo, read `CONTRIBUTING.md`, find the current Slack
> toolkit's send-message tool and how it handles upstream errors, and check open issues. Adapt the
> sketch below to the real function names and error classes rather than pasting it.

## Option A (small, high-signal PR): verify-then-retry for Slack sends

**Problem.** If `chat.postMessage` returns a 5xx or times out *after* Slack accepted the message,
a retry (by the framework, the client or the model) double-posts. toolproof reproduces this
deterministically with its chaos twin.

**Change.** On an ambiguous failure, check recent history (or thread replies) for a message with
the same author + text before re-posting; otherwise raise a retryable error with guidance.

```python
# Sketch: map to the toolkit's real helpers / error types.
async def _send_once_safely(client, channel_id: str, text: str, thread_ts: str | None) -> dict:
    for attempt in range(3):
        try:
            return await client.chat_postMessage(channel=channel_id, text=text, thread_ts=thread_ts)
        except AmbiguousUpstreamError:          # 5xx / timeout after the request was sent
            existing = await _find_own_recent_message(client, channel_id, text, thread_ts)
            if existing:
                return existing                 # it landed; don't post again
    raise RetryableToolError(
        "Slack failed to confirm the message.",
        additional_prompt_content="Check the channel before retrying to avoid a duplicate post.",
    )
```

**Tests.** Unit test with a fake client whose first call "applies then raises", asserting exactly
one message exists afterwards.

## Option B (eval contribution)

Arcade's eval framework already grades tool calls with critics. Propose an **outcome/side-effect
critic**: given a before/after snapshot (or mutation log) from a test double, score whether the
required state change happened and penalise unrequested changes ("blast radius"). toolproof's
`grading.py` is a working reference implementation to adapt.

## Option C (docs/examples)

Add an example showing how to point an Arcade eval suite at a local fault-injecting HTTP double, so
toolkit authors can test 429 / 5xx / pagination handling without live credentials.

**Suggested order:** open an issue describing A with the toolproof reproduction, ask maintainers
whether they'd like a PR, then send it. A small, well-tested PR merged beats a large one ignored.
