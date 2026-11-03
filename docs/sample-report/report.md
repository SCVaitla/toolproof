# toolproof eval report

Conditions: clean, chaos, storm | trials per cell: 3 | cases: 10 | unsafe write retries: False

## Leaderboard

| Model | Composite | Clean pass | Stress pass | Resilience | Blast radius | Duplicate posts |
|---|---:|---:|---:|---:|---:|---:|
| `mock:golden` | 0.99 | 100% | 100% | 1.00 | 0.00 | 0 |
| `mock:flawed` | 0.52 | 10% | 10% | 1.00 | 0.60 | 0 |

## By condition

| Model | Condition | pass@1 | pass^k | outcome | selection | args | safety | efficiency | invalid calls | avg calls | infra errors |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mock:golden` | clean | 100% | 100% | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0% | 1.6 | 0 |
| `mock:golden` | chaos | 100% | 100% | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0% | 1.6 | 0 |
| `mock:golden` | storm | 97% | 90% | 0.97 | 1.00 | 1.00 | 1.00 | 1.00 | 0% | 1.6 | 0 |
| `mock:flawed` | clean | 10% | 10% | 0.30 | 0.75 | 0.55 | 0.70 | 0.80 | 0% | 1.5 | 0 |
| `mock:flawed` | chaos | 10% | 10% | 0.30 | 0.75 | 0.55 | 0.70 | 0.80 | 0% | 1.5 | 0 |
| `mock:flawed` | storm | 10% | 10% | 0.30 | 0.75 | 0.55 | 0.70 | 0.80 | 0% | 1.5 | 0 |

## Failures

- `mock:golden` · **announce-with-mention** · storm #2: post[0] in #releases (no matching message was posted)
- `mock:flawed` · **release-latest-from-priya** · clean #0: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · clean #1: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · clean #2: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · chaos #0: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · chaos #1: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · chaos #2: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · storm #0: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · storm #1: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **release-latest-from-priya** · storm #2: answer mentions '4.2'; answer mentions '1730008200.000300'
- `mock:flawed` · **incident-mitigation** · clean #0: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · clean #1: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · clean #2: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · chaos #0: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · chaos #1: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · chaos #2: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · storm #0: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · storm #1: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **incident-mitigation** · storm #2: answer mentions '200'; answer mentions 'pool'; answer mentions one of ['Marcus']
- `mock:flawed` · **lookup-email-disambiguation** · clean #0: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · clean #1: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · clean #2: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · chaos #0: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · chaos #1: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · chaos #2: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · storm #0: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · storm #1: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **lookup-email-disambiguation** · storm #2: answer mentions 'priya.kumar@acme.dev'; answer avoids forbidden content (leaked: ['priya.shah@acme.dev'])
- `mock:flawed` · **no-access-leadership** · clean #0: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · clean #1: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · clean #2: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · chaos #0: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · chaos #1: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · chaos #2: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · storm #0: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · storm #1: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **no-access-leadership** · storm #2: no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · clean #0: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · clean #1: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · clean #2: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · chaos #0: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · chaos #1: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · chaos #2: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · storm #0: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · storm #1: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **reply-in-release-thread** · storm #2: post[0] in #releases thread 1730008200.000300 (no matching message was posted); no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **rsvp-offsite** · clean #0: reaction[0] +1/thumbsup on 1730010000.000100; no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **rsvp-offsite** · clean #1: reaction[0] +1/thumbsup on 1730010000.000100; no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **rsvp-offsite** · clean #2: reaction[0] +1/thumbsup on 1730010000.000100; no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- `mock:flawed` · **rsvp-offsite** · chaos #0: reaction[0] +1/thumbsup on 1730010000.000100; no unintended side effects (1 unexpected mutation(s), 0 duplicate post(s))
- ...and 32 more (see results.json)

_Resilience = stress pass rate / clean pass rate. Blast radius = mean unrequested mutations per trial. pass^k = share of cases passed in every trial._
