#!/usr/bin/env python3
"""Report prompt-cache usage for a Codex/ChatGPT desktop session.

Reads the session rollout JSONL that the app already writes under
~/.codex/sessions/ and surfaces the cache fields that the built-in
/status panel receives but does not display.

Usage:
  cachestat.py                 # newest session
  cachestat.py --last N        # newest, plus per-turn history (N turns)
  cachestat.py <session-id>    # a specific session (full or prefix)
  cachestat.py --list          # list recent sessions
  cachestat.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SESSIONS = Path.home() / ".codex" / "sessions"

FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def rollouts():
    if not SESSIONS.is_dir():
        return []
    return sorted(SESSIONS.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def pick(session_id: str | None) -> Path | None:
    files = rollouts()
    if not files:
        return None
    if not session_id:
        return files[0]
    for f in files:
        if session_id in f.name:
            return f
    return None


def parse(path: Path):
    """Return (last_snapshot, turns, session_meta). Tolerates partial lines."""
    snapshots, meta = [], {}
    with path.open("r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or "token" not in line and "session" not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            payload = rec.get("payload") or {}
            if not meta and payload.get("type") in ("session_meta", "session_created"):
                meta = payload
            info = payload.get("info") or {}
            if isinstance(info, dict) and "total_token_usage" in info:
                snapshots.append(
                    {
                        "total": info.get("total_token_usage") or {},
                        "last": info.get("last_token_usage") or {},
                        "context_window": info.get("model_context_window"),
                        "rate_limits": payload.get("rate_limits") or {},
                        "ts": rec.get("timestamp"),
                    }
                )
    return (snapshots[-1] if snapshots else None), snapshots, meta


def n(v):
    return v if isinstance(v, (int, float)) else 0


def pct(part, whole):
    return (100.0 * part / whole) if whole else 0.0


def bar(fraction, width=28):
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "█" * filled + "·" * (width - filled)


def fmt(v):
    return f"{int(v):,}"


def human_reset(epoch):
    if not epoch:
        return "unknown"
    delta = epoch - time.time()
    if delta <= 0:
        return "now"
    hours, mins = divmod(int(delta // 60), 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {mins}m"
    return f"in {mins}m"


def report(path: Path, snap, turns, meta, history: int):
    total, last = snap["total"], snap["last"]
    t_in, t_cached = n(total.get("input_tokens")), n(total.get("cached_input_tokens"))
    t_write = n(total.get("cache_write_input_tokens"))
    t_fresh = max(0, t_in - t_cached)
    hit = pct(t_cached, t_in)

    print()
    print("  Prompt cache usage")
    print(f"  session   {path.stem.split('-', 1)[-1]}")
    print()
    print(f"  Cache hit rate   {bar(hit / 100)}  {hit:5.1f}%")
    print(f"  Cached input     {fmt(t_cached):>14}  (billed at cache-read rate)")
    print(f"  Fresh input      {fmt(t_fresh):>14}  (billed at full rate)")
    if t_write:
        print(f"  Cache writes     {fmt(t_write):>14}")
    print(f"  Total input      {fmt(t_in):>14}")
    print()
    print(f"  Output           {fmt(n(total.get('output_tokens'))):>14}"
          f"   (reasoning {fmt(n(total.get('reasoning_output_tokens')))})")
    print(f"  Session total    {fmt(n(total.get('total_tokens'))):>14}")

    win = snap.get("context_window")
    used = n(last.get("total_tokens"))
    if win:
        used_pct = pct(used, win)
        print()
        print(f"  Context window   {bar(used_pct / 100)}  {used_pct:5.1f}%")
        print(f"                   {fmt(used)} used / {fmt(win)}"
              f"   ({fmt(max(0, win - used))} left)")

    l_in, l_cached = n(last.get("input_tokens")), n(last.get("cached_input_tokens"))
    if l_in:
        print()
        print(f"  Last turn        {pct(l_cached, l_in):5.1f}% cached"
              f"   ({fmt(l_cached)} / {fmt(l_in)} input)")

    rl = (snap.get("rate_limits") or {}).get("primary") or {}
    if rl:
        used_p = n(rl.get("used_percent"))
        mins = n(rl.get("window_minutes"))
        window = f"{int(mins / 1440)}d" if mins >= 1440 else f"{int(mins / 60)}h"
        print()
        print(f"  {window} limit        {bar(used_p / 100)}  {100 - used_p:5.1f}% left"
              f"   (resets {human_reset(rl.get('resets_at'))})")

    if history and len(turns) > 1:
        print()
        print(f"  Recent turns (newest last)")
        for s in turns[-history:]:
            li, lc = n(s["last"].get("input_tokens")), n(s["last"].get("cached_input_tokens"))
            if not li:
                continue
            print(f"    {bar(pct(lc, li) / 100, 20)}  {pct(lc, li):5.1f}%"
                  f"   {fmt(lc):>10} / {fmt(li):<10} in"
                  f"   +{fmt(n(s['last'].get('output_tokens'))):>7} out")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("session", nargs="?", help="session id or prefix")
    ap.add_argument("--list", action="store_true", help="list recent sessions")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--last", type=int, default=0, metavar="N", help="show N recent turns")
    args = ap.parse_args()

    if args.list:
        for f in rollouts()[:15]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            print(f"{ts}  {f.stem.split('-', 1)[-1]}")
        return 0

    path = pick(args.session)
    if not path:
        print("No session rollout found under ~/.codex/sessions", file=sys.stderr)
        return 1

    snap, turns, meta = parse(path)
    if not snap:
        print(f"No token-usage records yet in {path.name}", file=sys.stderr)
        return 1

    if args.json:
        total, last = snap["total"], snap["last"]
        t_in, t_cached = n(total.get("input_tokens")), n(total.get("cached_input_tokens"))
        json.dump(
            {
                "session_file": str(path),
                "cache_hit_rate": round(pct(t_cached, t_in), 2),
                "cached_input_tokens": t_cached,
                "fresh_input_tokens": max(0, t_in - t_cached),
                "total": total,
                "last_turn": last,
                "context_window": snap.get("context_window"),
                "rate_limits": snap.get("rate_limits"),
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    report(path, snap, turns, meta, args.last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
