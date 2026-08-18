---
name: cache-status
description: Report prompt cache usage for the current Codex session — cache hit rate, cached vs. fresh input tokens, context window usage, per-turn cache history, and rate limits. Use when the user asks about prompt caching, cache hit rate, cached tokens, token usage, or wants the /status numbers with cache detail (e.g. "how much caching am I getting", "status with cache", "cache hit rate", "@cache-status").
---

# Cache Status

Surfaces the prompt-cache numbers that the built-in `/status` panel receives but does not
display. The desktop app tracks `cached_input_tokens` per turn and writes it to the session
rollout, but only forwards `{percent, usedTokens, contextWindow}` to the status UI.

## Usage

Run the reporter and show its output verbatim in a fenced block — the alignment and bars are
part of the report:

```bash
python3 scripts/cachestat.py
```

Add recent per-turn history when the user wants a trend (how caching behaved over the session):

```bash
python3 scripts/cachestat.py --last 8
```

Other modes:

| Command | Purpose |
| --- | --- |
| `python3 scripts/cachestat.py --json` | Machine-readable, for further computation |
| `python3 scripts/cachestat.py --list` | List recent sessions with timestamps |
| `python3 scripts/cachestat.py <session-id>` | Report on a specific session (id or prefix) |

Paths are relative to this skill's root. If the working directory differs, resolve the script
path against the skill directory rather than guessing.

## Reading the output

- **Cache hit rate** — `cached_input_tokens / input_tokens` across the whole session. Cached
  input is a strict subset of total input, billed at the cheaper cache-read rate.
- **Fresh input** — the remainder, billed at the full input rate. This is the number that
  actually costs money as a conversation grows.
- **Last turn** — cache rate for the most recent request. In a long steady conversation this
  typically runs high (95%+); it drops sharply right after a context edit, a compaction, or a
  new session, because the prefix changed and had to be re-sent.
- **Context window** — matches the `used / total` figure in the built-in `/status` panel.

A falling per-turn rate across the history usually means something is invalidating the cached
prefix each turn. A single low turn after a long high run is normally just a cache write.

## Notes

- Reads only local files under `~/.codex/sessions`; no network calls.
- If the user asks for cache data on a session that has not made a request yet, the rollout has
  no token-usage record and the script reports that rather than inventing numbers.
- Report the numbers as given. Do not estimate cost in currency unless the user supplies rates,
  since per-model cache-read pricing is not available locally.
