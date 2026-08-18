#!/usr/bin/env python3
"""Measure what actually busts the Codex prompt cache.

Walks session rollouts in event order, tracks reasoning effort / model / compaction,
and reports the cache hit rate of the turn immediately following each event class
against the session's own baseline.

Usage:
  cache-experiment.py                 # aggregate across all local sessions
  cache-experiment.py --detail        # per-session timelines for changed sessions
  cache-experiment.py --types         # discover payload types (schema probe)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

CODEX = Path.home() / ".codex"


def rollouts():
    return sorted(CODEX.rglob("rollout-*.jsonl"))


def walk(path: Path):
    """Yield (kind, data) in file order. kind in {ctx, usage, compact}."""
    effort = model = None
    with path.open(errors="replace") as fh:
        for line in fh:
            if '"turn_context"' not in line and "token_count" not in line and "compact" not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            p = rec.get("payload") or {}
            # Mixed schema: token_count arrives wrapped in a generic {"type":"event_msg"}
            # envelope with the real type in the payload, while turn_context carries its
            # type at record level and has no payload type. Payload wins, record is fallback.
            t = p.get("type") or rec.get("type")

            if t == "turn_context":
                new = (p.get("effort"), p.get("model"))
                changed = (effort, model) != (None, None) and new != (effort, model)
                effort, model = new
                yield "ctx", {"effort": effort, "model": model, "changed": changed}

            elif t == "token_count":
                info = p.get("info") or {}
                last = info.get("last_token_usage") or {}
                if last.get("input_tokens"):
                    yield "usage", {
                        "input": last["input_tokens"],
                        "cached": last.get("cached_input_tokens", 0) or 0,
                        "effort": effort,
                        "model": model,
                    }

            elif t and "compact" in str(t).lower():
                yield "compact", {"type": t}


def classify(path: Path):
    """Label each turn with the event that preceded it."""
    pending = None  # 'effort', 'compact'
    turns = []
    first = True
    for kind, d in walk(path):
        if kind == "ctx" and d["changed"]:
            # A change before the first request is a session-start setting, not a
            # mid-conversation switch. Only the latter can bust a warm cache.
            pending = "effort_at_start" if first else "effort"
        elif kind == "compact":
            pending = "compact"
        elif kind == "usage":
            rate = 100.0 * d["cached"] / d["input"]
            if first:
                trigger = "effort_at_start" if pending == "effort_at_start" else "cold"
            else:
                trigger = pending or "steady"
            turns.append(
                {
                    "rate": rate,
                    "input": d["input"],
                    "cached": d["cached"],
                    "effort": d["effort"],
                    "trigger": trigger,
                }
            )
            first = False
            pending = None
    return turns


def summarize(groups, label):
    rates = [t["rate"] for t in groups]
    if not rates:
        return None
    lost = sum(t["input"] - t["cached"] for t in groups)
    return {
        "label": label,
        "n": len(rates),
        "mean": statistics.mean(rates),
        "median": statistics.median(rates),
        "busted": sum(1 for r in rates if r < 50),
        "lost_tokens": lost,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--types", action="store_true")
    args = ap.parse_args()

    files = rollouts()
    if args.types:
        types = Counter()
        for f in files:
            for line in f.open(errors="replace"):
                if "compact" not in line:
                    continue
                try:
                    t = json.loads(line).get("payload", {}).get("type")
                except ValueError:
                    continue
                if t:
                    types[t] += 1
        for t, c in types.most_common(25):
            print(f"{c:6}  {t}")
        return 0

    buckets = {"cold": [], "effort": [], "compact": [], "steady": []}
    changed_sessions = []

    for f in files:
        turns = classify(f)
        if not turns:
            continue
        if any(t["trigger"] == "effort" for t in turns):
            changed_sessions.append((f, turns))
        for t in turns:
            buckets.setdefault(t["trigger"], []).append(t)

    print(f"\n  Analyzed {len(files)} sessions, "
          f"{sum(len(v) for v in buckets.values()):,} turns\n")
    print(f"  {'event preceding turn':<24}{'turns':>7}{'mean hit':>10}"
          f"{'median':>9}{'<50%':>7}{'full-rate tokens':>19}")
    print("  " + "-" * 76)
    for key, label in [
        ("steady", "steady (no change)"),
        ("effort", "MID-conversation effort"),
        ("compact", "after compaction"),
        ("effort_at_start", "effort set at start"),
        ("cold", "first turn (cold)"),
    ]:
        s = summarize(buckets.get(key, []), label)
        if not s:
            print(f"  {label:<24}{0:>7}{'--':>10}{'--':>9}{'--':>7}"
                  f"{'no such turns in data':>19}")
            continue
        print(f"  {s['label']:<24}{s['n']:>7}{s['mean']:>9.1f}%{s['median']:>8.1f}%"
              f"{s['busted']:>7}{s['lost_tokens']:>19,}")

    if args.detail and changed_sessions:
        print(f"\n  Sessions with an effort change ({len(changed_sessions)}):\n")
        for f, turns in changed_sessions[:12]:
            print(f"  {f.name[:56]}")
            for i, t in enumerate(turns):
                mark = {"effort": " <-- EFFORT CHANGE", "compact": " <-- COMPACTED",
                        "cold": " (cold start)"}.get(t["trigger"], "")
                if t["trigger"] in ("effort", "compact", "cold") or (
                    i and turns[i - 1]["trigger"] in ("effort", "compact")
                ):
                    print(f"      {t['rate']:5.1f}%  {t['cached']:>9,}/{t['input']:<9,}"
                          f"  {str(t['effort']):<6}{mark}")
            print()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
