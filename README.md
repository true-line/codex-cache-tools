# codex-cache-tools

Surface and analyze prompt-cache usage in the ChatGPT desktop app (Codex).

The app's built-in `/status` panel shows context usage and rate limits but **omits
cache data entirely** — even though the record it renders from already contains
`cached_input_tokens`, and the renderer holds a full `tokenUsage` object with
`cachedInputTokens` that it uses only for telemetry.

These tools read the session rollout JSONL that Codex already writes under
`~/.codex/sessions/`, so they need no network access and modify nothing inside
the signed application bundle.

## Why this matters

Every request re-sends the entire conversation. A session whose context has grown
to ~140K tokens, across 34 requests, ships **3.2M cumulative input tokens** — the
per-turn inputs sum exactly to the cumulative figure. Nearly all of that is an
identical prefix, so it is cache-eligible:

```
Cache hit rate   ███████████████████████████·   95.4%
Cached input          3,072,512  (billed at cache-read rate)
Fresh input             149,106  (billed at full rate)
Total input           3,221,618
```

Full-rate billing on 149K tokens instead of 3.2M — a ~22× difference on input.
The cache does nearly all the work, and the built-in panel gives you no way to
see it, or to notice when it stops working.

## Finding: changing reasoning effort mid-conversation destroys the cache

Mined from 238 local session rollouts / 34,070 turns:

| Event preceding turn | Turns | Mean hit | Median | Busted (<50%) |
| --- | ---: | ---: | ---: | ---: |
| Steady (no change) | 33,557 | **96.5%** | 98.9% | 324 |
| **Mid-conversation effort change** | 59 | **12.6%** | 10.4% | **58 of 59** |
| After compaction | 218 | 46.7% | 43.3% | 154 |
| First turn (cold) | 232 | 43.8% | 48.5% | 126 |

An effort switch is **worse than starting a new session**. Across those 59
switches it burned 6,605,222 full-rate tokens, averaging ~112K per switch.

### The mechanism

Tokens still cached on the turn following an effort change:

```
11,008 tokens  x56 turns
 9,984 tokens  x1
39,680 tokens  x1
71,424 tokens  x1
```

**56 of 59 retained exactly 11,008 tokens** — while context at the moment of
change ranged from 36K to 213K. That fixed floor is the static preamble (system
prompt + tool definitions). Everything after it is re-sent at full price.

The hit *rate* therefore only looks worse in large contexts because the survivor
is pinned while the denominator grows: 27.6% at 36K context, 5.2% at 213K. The
absolute damage model is the same throughout, and the cost of a switch is:

```
cost = context_size - 11,008
```

### What follows

- **Compact before switching effort, never after.** Compacting can't prevent the
  bust — nothing can, since it's caused by the request parameter and not by the
  conversation content — but the cost scales with context size. Compacting 200K
  down to 40K before a switch turns a ~191K full-rate re-send into ~29K.
- **Forking first does nothing on its own.** A fork preserves the conversation,
  so the re-send is the same size. It helps only if you fork from an earlier
  point, which is just truncation by another name.
- **Set effort at session start when you can**, and batch changes rather than
  toggling back and forth.

Caveats: only 1 of the 59 changes immediately followed a compaction, so the
"compact first" guidance is derived from the mechanism rather than a direct A/B.
All observed transitions were `high → ultra` on `gpt-5.6-sol`, so the magnitude
may vary for other transitions.

## Tools

### `tools/cachestat.py`

Per-session cache report: hit rate, cached vs. fresh input, context window,
per-turn history, and rate limits.

```bash
python3 tools/cachestat.py              # newest session
python3 tools/cachestat.py --last 8     # with per-turn history
python3 tools/cachestat.py --json       # machine-readable
python3 tools/cachestat.py --list       # recent sessions
```

### `tools/cache-experiment.py`

Mines every local rollout and reports cache hit rate bucketed by the event that
preceded each turn, so you can measure what actually busts the cache on your own
usage rather than trusting anyone's claims.

```bash
python3 tools/cache-experiment.py
python3 tools/cache-experiment.py --detail   # per-session timelines
python3 tools/cache-experiment.py --types    # schema probe
```

### `tools/status-cache-inject.js` (unsupported, untested)

Adds a `Cache:` row to the actual `/status` panel by injecting into the Electron
main process via `NODE_OPTIONS`, so nothing in the app bundle is modified and its
code signature stays valid.

```bash
NODE_OPTIONS="--require $HOME/.codex/tools/status-cache-inject.js" \
  /Applications/ChatGPT.app/Contents/MacOS/ChatGPT
```

This one is **untested** and inherently fragile: it clones the `Context:` row's
DOM structure, which minified builds can change at any update. The plugin below
is the durable option.

## Plugin

`plugin/cache-status/` is a Codex plugin exposing the reporter as a first-class,
`@`-mentionable plugin with its own composer entry.

```bash
cp -R plugin/cache-status ~/plugins/
codex plugin add cache-status@personal
```

Requires a personal marketplace at `~/.agents/plugins/marketplace.json` pointing
at the plugin. Reload the app afterward; a running instance won't pick up a newly
installed plugin mid-session.

**Note:** slash commands are a closed set compiled into the app bundle — the
plugin manifest has no `commands` field, and its `apps` field is only a
connector-ID mapping. `/statusWithCache` is not achievable without modifying the
signed bundle. `@cache-status` is the supported equivalent.

## Rollout schema gotcha

If you parse these logs yourself: the schema is mixed. `token_count` arrives
wrapped in a generic `{"type": "event_msg"}` envelope with the real type in the
payload, while `turn_context` carries its type at *record* level and has no
payload type. Checking only `payload.type` silently yields zero `turn_context`
events — and a confident, wrong conclusion that effort changes are harmless.
Resolve as `payload.type or record.type`.

## Requirements

Python 3.9+, macOS, ChatGPT desktop app. No third-party dependencies.

The statistics above were generated from the author's own local sessions.

## License

MIT
