# claude-code-spend

[![tests](https://github.com/alexandersnakes/claude-code-spend/actions/workflows/ci.yml/badge.svg)](https://github.com/alexandersnakes/claude-code-spend/actions/workflows/ci.yml)

One Python file that reads the transcripts Claude Code already writes on your
machine and tells you what your agent sessions cost - and, more to the point,
what the money actually bought.

I ran it over 23 days of my own logs expecting to find long sessions, or an
expensive model. What I found instead: of a $21,442 bill, **$9,478 went on
re-reading tool results that were already in the context** - 44.2% of
everything. A file read once at the start of a session is paid for again on
every step that follows it, to the end of that session. One 160,008-token read
had 2,158 steps behind it and cost $173 by itself.

```
Re-reading tool results
----------------------------------------------------
  results in the main context                  46,654
    tokens in them                         31,118,699
  amortized cost                               $9,478
    share of the bill                           44.2%
    re-read multiplier                           ×609
  average result, tokens                          667
    what it costs                               $0.20
  priciest single read, tokens                160,008
    steps that followed it                      2,158
    cost                                         $173
```

## Running it

```
curl -O https://raw.githubusercontent.com/alexandersnakes/claude-code-spend/main/agent_spend.py
python agent_spend.py --from 2026-08-01 --to today
```

Nothing to install. Standard library only, Python 3.9 or newer, one file you
can read in a sitting. It looks at `~/.claude/projects/**/*.jsonl`, the
transcripts Claude Code writes as it works, and at nothing else. `--root` aims
it somewhere else, `--json` prints the same numbers machine-readable, and
`--compare-from` / `--compare-to` sets an earlier period beside the current one.

## The whole output

```
2026-08-01 ... 2026-08-23 (23 days), prices as of 2026-08-24


Cost
----------------------------------------------------
  cost for the period                         $21,442
    per day                                      $932

What the money bought
----------------------------------------------------
  reading the cache                             63.9%
  writing the cache                             26.7%
    of that, the 1-hour cache                   83.9%
  output                                         9.3%
  fresh input                                    0.1%

Context
----------------------------------------------------
  steps, main sessions                         80,172
    context per step                          326,979
  steps, subagents                             17,571
    context per step                          111,354
  subagents' share of steps                     18.0%
  subagents' share of tokens                     7.0%

Sessions
----------------------------------------------------
  sessions                                        185
  median session                               $58.26
  sessions over $100                               64
    their share of the bill                     80.8%
```

Two things in there took me a while to accept. Writing new text is 9.3% of the
bill and fresh input is 0.1% - the thinking is nearly free, and carrying the
context is nearly everything. And the spend is lopsided: 64 sessions out of 185
account for 80.8% of it, against a median session of $58.26.

## How the re-read number is built

A step is one `assistant` record carrying `message.usage`. A tool result that
arrives at step *t* of a session stays in the context for every step after it,
and each of those steps pays the cache-read rate to carry it again:

```
cost = tokens in the result × steps after it × $0.50 per million
```

Sum that over every tool result in every main session and you get the $9,478.
The multiplier is that sum divided by what the same tokens would have cost if
they had been read exactly once - so ×609 says that, weighted by size, a tool
result was carried for another 609 steps after the one that produced it.

That is a model of the cost, not an invoice line. What it captures is real
though: the context is re-sent on every step, and a long session is mostly
paying rent on things it read hours ago.

## What I changed after seeing this

Two habits, both written up here because the counter on its own only tells you
that you have a problem:

- [practices/delegation-rule.md](practices/delegation-rule.md) - a numeric
  threshold for when a big read should go to a subagent instead of into the
  main context, and the arithmetic the threshold comes from.
- [practices/scout-agent.md](practices/scout-agent.md) - the read-only search
  agent I send those reads to, on a cheaper model, with the one line of
  configuration that decides whether it can write to your files.

Did they work? **Not enough data yet.** The rules took effect on 2026-08-24,
and that same day was spent testing them, so it cannot serve as the "after"
window. The first honest comparison is possible on **2026-08-28**, when
2026-08-25 through 2026-08-27 have become three full days:

```
python agent_spend.py --from 2026-08-25 --to 2026-08-27 \
       --compare-from 2026-08-01 --compare-to 2026-08-23
```

Read the per-day rows, not the period totals - the windows are different
lengths. And even then the comparison will not be clean: different days hold
different work, and the counter cannot separate a change of habit from a change
of task.

## Limits, and what I would not claim

- **Prices are constants with a date on them** (2026-08-24, in the file).
  That is deliberate - a before/after comparison has to be priced at one rate -
  and it means the file goes stale whenever the price list moves.
- **These dollars are API list prices applied to the token counts in your
  logs.** On a subscription plan you are not billed per token, and then the
  number is a measure of load, not a bill.
- **A step is a record, not an API call.** One call writes several records:
  over this window, 80,172 records came from 43,808 calls, a factor of 1.83.
  Anything counted per step - the ×609 among it - is inflated by roughly that
  much against physical calls.
- **Tool-result size is characters ÷ 4**, an estimate rather than a tokenizer.
- **An unknown model is billed at the Opus rate**, and `<synthetic>` records
  are free. New model names show up faster than this file gets updated.
- **Compaction is not modelled.** When a long session is compacted, old results
  stop being carried, and this counter keeps charging for them. On very long
  sessions the re-read number is high for that reason.
- These are one machine's numbers and one person's way of working. The question
  worth answering is not whether you also get 44% - it is what your own number
  is, and the tool exists so you can find out in about two minutes.

## What else is in here

[TRAPS.md](TRAPS.md) collects eight things that cost me a separate experiment
each - agent definitions cached at session start, an alias that resolves to an
older model, a read-only agent that could write files. Each one is dated,
because at least one of them already changed once during the month it was
measured in.

The tests build small transcript corpora in a temporary directory and check the
arithmetic against the published rates: `python -m unittest discover -s tests`.

## Changes

If your numbers come out very different from mine, that is the interesting
case - open an issue with the output and the shape of the work behind it.
Patches are welcome as long as the tool stays a single file with no
dependencies, and any change to how a number is computed comes with a test.

MIT licensed.
