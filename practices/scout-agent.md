# The scout: a read-only agent for searching

The counterpart to [delegation-rule.md](delegation-rule.md). That rule says
when to send a read away; this is what to send it to.

The job is narrow on purpose: sweep a lot of files, come back with where things
are. It locates code, it does not review or audit it, and it never edits
anything. Everything below follows from that one sentence.

Save it as `~/.claude/agents/Explore.md`:

```markdown
---
name: Explore
description: Read-only search agent for broad fan-out searches - when answering
  means sweeping many files, directories, or naming conventions and you only
  need the conclusion, not the file dumps. It reads excerpts rather than whole
  files, so it locates code; it does not review or audit it. Say how wide to
  search: "medium" for moderate exploration, "very thorough" for multiple
  locations and naming conventions.
model: claude-sonnet-5
tools: Glob, Grep, Read, Bash, WebFetch, WebSearch
---

You are a read-only reconnaissance agent. You find where things are; you do not
change them and you do not review them.

How to work:

- Search broadly first (Glob, Grep), then read only the excerpts you need.
  Never read a whole large file when a grep with context answers the question.
- Follow the chain when an answer spans more than one file - a partial answer
  from a single file is the most common way this job is done wrong.
- Report conclusions with `path:line` references, not file dumps. The session
  that called you is paying to re-read every line you return, on every step it
  takes afterwards. Return the finding, not the evidence pile.
- If you could not establish something, say so plainly instead of guessing.
```

## The line that matters most

```yaml
tools: Glob, Grep, Read, Bash, WebFetch, WebSearch
```

**Leave `tools:` out and the agent gets the full set, Edit and Write among
them.** The prompt above says "read-only" three times, and that buys you
nothing: prose is a preference, the tool list is the permission. An agent
described as read-only that quietly rewrites a file while looking for it is
the worst version of this trap, because you will not go looking for the change.

If you want it to be able to run tests but not edit, that is still this list
with `Bash` in it and `Edit`/`Write` out.

## Why a cheaper model

Searching is not the part of the work that needs the strongest model - it needs
breadth and a willingness to grep twice. Putting the scout on a mid-tier model
is most of the saving; the rest comes from the fact that its context dies with
it instead of being carried by the session that called it.

Two things to know before you measure the effect, both learned the expensive
way and both in [TRAPS.md](../TRAPS.md): an alias like `sonnet` does not
necessarily mean the newest Sonnet, and a `model` argument at the call site
overrides what this file says. Check which model actually ran before you
attribute a saving to it.

## The description is not decoration

The `description` field is what the calling session matches against when it
decides whether to delegate at all. A vague one - "helps with searching" -
gets the agent ignored precisely when it was needed, and you go on paying for
the reads yourself. Name the shape of question it takes, and say what it will
not do.
