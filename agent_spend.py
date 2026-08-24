#!/usr/bin/env python
"""agent_spend.py - what your Claude Code sessions actually cost.

Reads the transcripts under ~/.claude/projects/**/*.jsonl and reports what a
period cost: the bill, what the bill is made of, how much context each step
carries, and how much of the money went on re-reading tool results that were
already in the context.

    python agent_spend.py --from 2026-08-01 --to 2026-08-23
    python agent_spend.py --from 2026-08-24 --to today \
                          --compare-from 2026-08-01 --compare-to 2026-08-23
    python agent_spend.py --from 2026-08-24 --to today --json

Four choices that are deliberate and that you should know about before you
trust a number:
  * prices are constants with a date on them - a before/after comparison has
    to be priced at one rate, not at whatever the price list says today;
  * a step is an assistant record carrying message.usage; one API call writes
    several of those, so this step count runs above the number of API calls;
  * the date window is the machine's local day, not UTC;
  * an unknown model is billed at the Opus rate, <synthetic> is free.
"""

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

# --- prices, $ per million tokens --------------------------------------------
# Frozen on 2026-08-24. Not fetched over the network on purpose: comparing two
# periods only means something if both are priced at the same rate.
#
# A cache write has TWO rates, and the difference is not small change: the
# 5-minute cache costs 1.25x input, the 1-hour cache 2x. In the logs this was
# measured on, 83.9% of the writes were the 1-hour kind, so a single averaged
# rate understated the bill by roughly a quarter. The split lives in
# `usage.cache_creation` and was present in 100% of records (2026-08-24).
PRICES_DATE = "2026-08-24"
PRICES = {
    #         input  output  write 5m  write 1h  cache read
    "opus":   (5.00,  25.00,  6.25,   10.00,    0.50),
    "sonnet": (3.00,  15.00,  3.75,    6.00,    0.30),
    "haiku":  (1.00,   5.00,  1.25,    2.00,    0.10),
}
PRICE_PARTS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
CACHE_READ_RATE = PRICES["opus"][4] / 1e6   # rate re-reads are amortized at

DEFAULT_ROOT = pathlib.Path(os.path.expanduser("~")) / ".claude" / "projects"
EXPENSIVE_SESSION = 100.0   # what counts as an expensive session, $


def price_family(model):
    """Which price list a model falls under. None means it is not billed."""
    m = (model or "").lower()
    if m == "<synthetic>" or not m:
        return None
    for fam in ("opus", "sonnet", "haiku"):
        if fam in m:
            return fam
    return "opus"   # a model this script has not heard of: bill at the top rate


# --- the date window ---------------------------------------------------------

def parse_day(s):
    if s == "today":
        return datetime.now().date()
    if s == "yesterday":
        return datetime.now().date() - timedelta(days=1)
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit("cannot read the date %r - use YYYY-MM-DD, 'today' or 'yesterday'" % s)


def window(day_from, day_to):
    """Local days [day_from, day_to] -> UTC bounds as ISO strings.

    Strings get compared instead of parsing a timestamp on every record: these
    logs run past 500 MB, and parsing each one is the whole runtime.
    """
    lo = datetime(day_from.year, day_from.month, day_from.day)
    hi = datetime(day_to.year, day_to.month, day_to.day) + timedelta(days=1)
    to_utc = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return to_utc(lo), to_utc(hi)


# --- collecting --------------------------------------------------------------

def split_cache_write(u):
    """Cache write -> (1-hour, 5-minute). The two always add up to the headline
    `cache_creation_input_tokens`: in 12 records out of 97,597 the breakdown
    disagreed with the headline, and there the headline wins and the difference
    goes to the cheaper 5-minute rate. No breakdown at all: all 5-minute.
    """
    total = u.get("cache_creation_input_tokens") or 0
    cc = u.get("cache_creation")
    hour = (cc.get("ephemeral_1h_input_tokens") or 0) if isinstance(cc, dict) else 0
    hour = min(hour, total)
    return hour, total - hour


class Bucket:
    __slots__ = ("steps", "inp", "cw1h", "cw5m", "cread", "out", "cost")

    def __init__(self):
        self.steps = self.inp = self.cw1h = self.cw5m = self.cread = self.out = 0
        self.cost = 0.0

    def add_usage(self, u, fam):
        i = u.get("input_tokens") or 0
        h, m5 = split_cache_write(u)
        r = u.get("cache_read_input_tokens") or 0
        o = u.get("output_tokens") or 0
        self.steps += 1
        self.inp += i
        self.cw1h += h
        self.cw5m += m5
        self.cread += r
        self.out += o
        if fam:
            pi, po, p5, p1h, pr = PRICES[fam]
            self.cost += (i * pi + o * po + m5 * p5 + h * p1h + r * pr) / 1e6

    @property
    def cwrite(self):
        return self.cw1h + self.cw5m

    @property
    def context(self):
        return self.inp + self.cwrite + self.cread

    @property
    def tokens(self):
        return self.context + self.out


def collect(root, lo, hi):
    """One pass over the corpus. Returns the raw tallies."""
    main, side = Bucket(), Bucket()
    cost_parts = [0.0, 0.0, 0.0, 0.0]      # input, output, cache write, cache read
    session_cost = {}                       # session -> $
    sessions_seen = set()
    tr_count = 0
    tr_tokens = 0.0
    tr_cost = 0.0
    tr_top = None                           # (tokens, steps ahead of it, $)
    files = 0

    for path in sorted(pathlib.Path(root).rglob("*.jsonl")):
        files += 1
        is_sub = path.parent.name == "subagents"
        # a session is a top-level file; its subagents sit in a folder beside it
        session = path.parent.parent.name if is_sub else path.stem
        bucket = side if is_sub else main
        steps_in_file = 0
        pending = []                        # tool results, held until the file ends

        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                has_usage = '"usage"' in line
                has_result = (not is_sub) and '"tool_result"' in line
                if not (has_usage or has_result):
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue

                kind = rec.get("type")
                if kind == "assistant":
                    msg = rec.get("message") or {}
                    usage = msg.get("usage")
                    if usage is None:
                        continue
                    steps_in_file += 1          # amortization counts the whole file
                    ts = (rec.get("timestamp") or "")[:19]
                    if not (lo <= ts < hi):
                        continue
                    fam = price_family(msg.get("model"))
                    before = bucket.cost
                    bucket.add_usage(usage, fam)
                    spent = bucket.cost - before
                    session_cost[session] = session_cost.get(session, 0.0) + spent
                    sessions_seen.add(session)
                    if fam:
                        pi, po, p5, p1h, pr = PRICES[fam]
                        u = usage
                        h, m5 = split_cache_write(u)
                        cost_parts[0] += (u.get("input_tokens") or 0) * pi / 1e6
                        cost_parts[1] += (u.get("output_tokens") or 0) * po / 1e6
                        cost_parts[2] += (m5 * p5 + h * p1h) / 1e6
                        cost_parts[3] += (u.get("cache_read_input_tokens") or 0) * pr / 1e6
                    continue

                if kind != "user" or is_sub or rec.get("isSidechain"):
                    continue
                ts = (rec.get("timestamp") or "")[:19]
                if not (lo <= ts < hi):
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    body = block.get("content")
                    size = 0 if body is None else len(json.dumps(body, ensure_ascii=False))
                    pending.append((size / 4.0, steps_in_file))

        # amortization: a result at step t is re-read (T - t) times after it
        total_steps = steps_in_file
        for tokens, at_step in pending:
            ahead = total_steps - at_step
            cost = tokens * ahead * CACHE_READ_RATE
            tr_count += 1
            tr_tokens += tokens
            tr_cost += cost
            if tr_top is None or cost > tr_top[2]:
                tr_top = (tokens, ahead, cost)

    return {
        "main": main, "side": side, "cost_parts": cost_parts,
        "session_cost": session_cost, "sessions": len(sessions_seen),
        "tr_count": tr_count, "tr_tokens": tr_tokens, "tr_cost": tr_cost,
        "tr_top": tr_top, "files": files,
    }


def metrics(raw, days):
    main, side = raw["main"], raw["side"]
    total_cost = main.cost + side.cost
    ci, co, cw, cr = raw["cost_parts"]
    costs = sorted(raw["session_cost"].values())
    n = len(costs)
    if n:
        median = costs[n // 2] if n % 2 else (costs[n // 2 - 1] + costs[n // 2]) / 2
    else:
        median = 0.0
    expensive = [c for c in costs if c > EXPENSIVE_SESSION]
    tr_tokens = raw["tr_tokens"]
    raw_read_cost = tr_tokens * CACHE_READ_RATE
    top = raw["tr_top"] or (0, 0, 0.0)
    pc = lambda part: 100.0 * part / total_cost if total_cost else 0.0

    return {
        "days": days,
        "cost_total": total_cost,
        "cost_per_day": total_cost / days if days else 0.0,
        "share_cache_read": pc(cr),
        "share_cache_write": pc(cw),
        "share_output": pc(co),
        "share_input": pc(ci),
        "cache_write_1h_share": (100.0 * (main.cw1h + side.cw1h) /
                                 (main.cwrite + side.cwrite)
                                 if (main.cwrite + side.cwrite) else 0.0),
        "cost_cache_read": cr, "cost_cache_write": cw,
        "cost_output": co, "cost_input": ci,
        "steps_main": main.steps,
        "steps_side": side.steps,
        "context_per_step_main": main.context / main.steps if main.steps else 0.0,
        "context_per_step_side": side.context / side.steps if side.steps else 0.0,
        "side_share_steps": 100.0 * side.steps / (main.steps + side.steps) if (main.steps + side.steps) else 0.0,
        "side_share_tokens": 100.0 * side.tokens / (main.tokens + side.tokens) if (main.tokens + side.tokens) else 0.0,
        "tool_results": raw["tr_count"],
        "tool_result_tokens": tr_tokens,
        "tool_result_cost": raw["tr_cost"],
        "tool_result_share": pc(raw["tr_cost"]),
        "reread_multiplier": raw["tr_cost"] / raw_read_cost if raw_read_cost else 0.0,
        "tool_result_avg_tokens": tr_tokens / raw["tr_count"] if raw["tr_count"] else 0.0,
        "tool_result_avg_cost": raw["tr_cost"] / raw["tr_count"] if raw["tr_count"] else 0.0,
        "top_read_tokens": top[0],
        "top_read_steps_ahead": top[1],
        "top_read_cost": top[2],
        "sessions": raw["sessions"],
        "session_cost_median": median,
        "sessions_over_100": len(expensive),
        "sessions_over_100_share": 100.0 * sum(expensive) / total_cost if total_cost else 0.0,
        "files_scanned": raw["files"],
    }


# --- printing ----------------------------------------------------------------

def days_word(n):
    """A one-day window would otherwise print "1 days" and read as a bug."""
    return "day" if n == 1 else "days"


def money(v):
    return "$" + format(int(round(v)), ",d") if abs(v) >= 100 else "$%.2f" % v


def num(v):
    return format(int(round(v)), ",d")


def pct(v):
    return "%.1f%%" % v


def mult(v):
    return "×%.0f" % v


ROWS = [
    ("--", "Cost",                                              None),
    ("cost_total",             "cost for the period",           money),
    ("cost_per_day",           "  per day",                     money),
    ("--", "What the money bought",                             None),
    ("share_cache_read",       "reading the cache",             pct),
    ("share_cache_write",      "writing the cache",             pct),
    ("cache_write_1h_share",   "  of that, the 1-hour cache",   pct),
    ("share_output",           "output",                        pct),
    ("share_input",            "fresh input",                   pct),
    ("--", "Context",                                           None),
    ("steps_main",             "steps, main sessions",          num),
    ("context_per_step_main",  "  context per step",            num),
    ("steps_side",             "steps, subagents",              num),
    ("context_per_step_side",  "  context per step",            num),
    ("side_share_steps",       "subagents' share of steps",     pct),
    ("side_share_tokens",      "subagents' share of tokens",    pct),
    ("--", "Re-reading tool results",                           None),
    ("tool_results",           "results in the main context",   num),
    ("tool_result_tokens",     "  tokens in them",              num),
    ("tool_result_cost",       "amortized cost",                money),
    ("tool_result_share",      "  share of the bill",           pct),
    ("reread_multiplier",      "  re-read multiplier",          mult),
    ("tool_result_avg_tokens", "average result, tokens",        num),
    ("tool_result_avg_cost",   "  what it costs",               money),
    ("top_read_tokens",        "priciest single read, tokens",  num),
    ("top_read_steps_ahead",   "  steps that followed it",      num),
    ("top_read_cost",          "  cost",                        money),
    ("--", "Sessions",                                          None),
    ("sessions",               "sessions",                      num),
    ("session_cost_median",    "median session",                money),
    ("sessions_over_100",      "sessions over $100",            num),
    ("sessions_over_100_share", "  their share of the bill",    pct),
]

LABEL_W = 38


def print_plain(m, header):
    print(header)
    print()
    for key, label, fmt in ROWS:
        if key == "--":
            print("\n%s" % label)
            print("-" * (LABEL_W + 14))
            continue
        print("  %-*s %12s" % (LABEL_W, label, fmt(m[key])))
    print()


def delta(was, now):
    if not was:
        return "—"
    d = 100.0 * (now - was) / was
    # the tenth is dropped only past 100%: printing "-100%" instead of "-99.7%"
    # would read as "down to nothing", which is not what happened
    return "%+.0f%%" % d if abs(d) >= 100 else "%+.1f%%" % d


def print_compare(now, was, header, was_label, now_label):
    print(header)
    print()
    print("  %-*s %12s   %12s  %8s" % (LABEL_W, "", "before", "after", "change"))
    print("  %-*s %12s   %12s" % (LABEL_W, "", was_label, now_label))
    for key, label, fmt in ROWS:
        if key == "--":
            print("\n%s" % label)
            print("-" * (LABEL_W + 42))
            continue
        print("  %-*s %12s → %12s  %8s" % (
            LABEL_W, label, fmt(was[key]), fmt(now[key]), delta(was[key], now[key])))
    print()


def run(root, d_from, d_to):
    lo, hi = window(d_from, d_to)
    days = (d_to - d_from).days + 1
    return metrics(collect(root, lo, hi), days)


def main(argv=None):
    # the console here is often cp1252, and both the table and the error text
    # break on it without this
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="What your Claude Code sessions cost over a period.")
    p.add_argument("--from", dest="d_from", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="d_to", required=True, metavar="YYYY-MM-DD|today")
    p.add_argument("--compare-from", dest="c_from", metavar="YYYY-MM-DD",
                   help="start of an earlier period to compare against")
    p.add_argument("--compare-to", dest="c_to", metavar="YYYY-MM-DD|today",
                   help="end of that earlier period")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--root", default=str(DEFAULT_ROOT),
                   help="directory holding the transcripts (default: %(default)s)")
    a = p.parse_args(argv)

    d_from, d_to = parse_day(a.d_from), parse_day(a.d_to)
    if d_to < d_from:
        raise SystemExit("--to is earlier than --from")
    root = pathlib.Path(a.root)
    if not root.is_dir():
        raise SystemExit("no transcript directory here: %s" % root)

    now = run(root, d_from, d_to)
    head = "%s ... %s (%d %s), prices as of %s" % (
        d_from, d_to, now["days"], days_word(now["days"]), PRICES_DATE)

    was = c_from = c_to = None
    if a.c_from or a.c_to:
        if not (a.c_from and a.c_to):
            raise SystemExit("--compare-from and --compare-to go together")
        c_from, c_to = parse_day(a.c_from), parse_day(a.c_to)
        if c_to < c_from:
            raise SystemExit("--compare-to is earlier than --compare-from")
        was = run(root, c_from, c_to)

    if a.json:
        out = {"period": {"from": str(d_from), "to": str(d_to), "days": now["days"]},
               "prices_date": PRICES_DATE,
               "prices": {fam: dict(zip(PRICE_PARTS, v)) for fam, v in PRICES.items()},
               "metrics": now}
        if was:
            out["compare"] = {"period": {"from": str(c_from), "to": str(c_to),
                                         "days": was["days"]},
                              "metrics": was,
                              "delta_pct": {k: (100.0 * (now[k] - was[k]) / was[k]
                                                if was[k] else None)
                                            for k in now}}
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    if was:
        span = lambda a, b: "%s…%s" % (a.strftime("%m-%d"), b.strftime("%m-%d"))
        print_compare(now, was, head, span(c_from, c_to), span(d_from, d_to))
    else:
        print_plain(now, head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
