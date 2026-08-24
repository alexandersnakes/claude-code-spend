# When a read should go to a subagent

Measured over 2026-08-01 … 2026-08-23: tool results sitting in the main context
cost $9,478 of a $21,442 bill - 44.2%, at a re-read multiplier of ×609. A file
you read once is paid for again on every step that follows, until the session
ends.

So the rule is: **before you pull a large file, a log, or a test run into the
main session, hand it to a subagent and take back the conclusion.** The subagent
reads into its own context, that context dies with it, and what comes back is a
paragraph.

## The threshold, as arithmetic rather than a mood

    expected tokens × steps left in the session > ~1.2 million  →  delegate

Where the 1.2 million comes from: carrying a result costs the cache-read rate,
$0.50 per million tokens, on every later step. So `tokens × steps` at that rate
is what the read will cost you:

    1,200,000 × $0.50 / 1,000,000  =  $0.60

and $0.60 is about what a small scout agent costs to run - they came out
between $0.30 and $1.50 on this machine. Below the threshold, reading directly
is cheaper than the agent that would save you the reading. Above it, the read
pays for the agent several times over.

The second factor is the one people forget. Early in a session there are
hundreds of steps ahead, so almost anything above a few thousand tokens crosses
the line; in the last few steps the same file is cheap to read directly. The
identical action has a different price depending on when you take it.

From the same measurement: one 160,008-token read with 2,158 steps behind it
cost $173. That single read would have paid for a hundred scouts.

## What to hand over, in practice

Grep sweeps across a repository. Whole log files. The output of a test suite
you only need a verdict from. Anything where you want the answer, not the
evidence pile - the evidence pile is precisely what you keep paying rent on.

What to keep in the main context: the file you are about to edit, and anything
you will need verbatim later. A conclusion that turns out to be wrong is far
more expensive than the read that would have prevented it, so do not delegate
the thing the work actually depends on.

## Which model to send

- A cheaper, faster model for "find it / count it / list it" - questions with
  no chain of reasoning behind them.
- The expensive model when an architectural decision will grow out of the
  answer, where a subtly wrong conclusion costs more than the tokens saved.

The scout that does this reading is in
[scout-agent.md](scout-agent.md), including the one line that decides whether
it can edit your files.

## Check your own number first

None of the above is worth adopting on my numbers. Run the counter over your
own logs:

```
python agent_spend.py --from 2026-08-01 --to today
```

If the share of the bill next to `amortized cost` is small for you, this rule
is not your problem, and you should go looking for whatever is.
