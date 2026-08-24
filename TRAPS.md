# Eight things that each cost a separate experiment

Every item here started as a session that behaved wrongly for reasons the logs
did not explain. They are written down as measurements, not as doctrine: each
one carries the date it was observed, one of them had already changed once
during the month it was measured in, and two of them only happen on Windows.

Measured between 2026-08-01 and 2026-08-24, on Claude Code as it stood on
2026-08-24, on one Windows 11 machine.

### Anything you print outside ASCII dies on a Windows console

Observed 2026-08-24. A script that printed a table with `×` and non-Latin
labels raised `UnicodeEncodeError` on Windows and printed nothing at all - not
even the error, because the error text was non-ASCII too. The console encoding
is the machine's code page, not UTF-8, and it applies to every stream.

The fix is two lines at the top of `main()`, and it is why they are in this
repository's script:

```python
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
```

The same thing bites from the other side: a shell pipeline that captures such
output will hand you replacement characters if it decodes with the code page.

### Agent definitions are read once, when the session starts

Observed 2026-08-24. Editing a file under `~/.claude/agents/` mid-session
changes nothing for that session: the definitions are loaded at start-up and
cached. You edit the model, run the agent again, watch it behave exactly as
before, and conclude the file is being ignored. It is not - it is simply the
version from an hour ago.

Restart the session after touching an agent file, or verify the change from a
fresh one before you believe any measurement about it.

### `sonnet` is an alias, and it did not point where I assumed

Observed 2026-08-24. Writing `model: sonnet` in an agent file resolved to
Sonnet 4.6 while the session itself was running Sonnet 5. Nothing warns you:
the agent runs, returns sensible work, and bills at a different rate than the
one you priced your delegation rule with.

Aliases track a channel, not the newest release. When the version matters -
and it matters whenever you are comparing costs - write the full model id.

### An agent file with no `tools:` line can write to your files

Observed 2026-08-24, and the one with the largest blast radius. A subagent
definition that omits `tools:` inherits the full tool set, Edit and Write
included. A file that describes a "read-only search agent" in its own prompt
is read-only by *prose*, not by permission, and the model is free to ignore
prose the moment a task seems to call for an edit.

State the list explicitly:

```yaml
tools: Glob, Grep, Read, Bash, WebFetch, WebSearch
```

### Three places set the model, and the one you edited may not win

Observed 2026-08-24. The model an agent runs on can come from the `model`
argument at the call site, from the `model:` line in the agent's own file, or
from the session it was spawned out of - in that order of seniority. A custom
file also takes precedence over a built-in agent of the same name, so a
definition you wrote months ago can quietly replace one that ships.

Consequence for cost measurement: an agent you believe is on a cheap model may
be inheriting an expensive one from the call site. Check the agent's own
records in the transcript before attributing a bill to a model.

### A hook that reads stdin hangs the session, with no error

Observed 2026-08-24. Hooks receive their payload on stdin. A hook command that
opens an interactive prompt, waits for a key, or otherwise reads more than it
was given will sit there forever, and the session that triggered it sits with
it. There is no timeout message and no failed tool call - the work simply stops
mid-step, which reads as a hung model rather than a hung hook.

Test a hook by running it standalone with its payload piped in, and give any
command inside it an explicit non-interactive flag.

### The built-in read limits are not constants

Observed changing between 2026-08-01 and 2026-08-24. How much of a file a read
returns, and where tool output gets truncated, are implementation details of
the running build - and they moved during that month. Any rule written against
a specific number ("read at most N lines and you are fine") silently stopped
being true, in the direction that costs money rather than the one that saves it.

Do not encode those limits in your own rules. Measure what the current build
does when the answer matters, and date the measurement, as this file does.

### On Windows, a command that needs elevation fails quietly

Observed 2026-08-24. Commands that require an elevated shell - service control,
scheduled-task changes, writes under protected paths - can return without an
error while having done nothing. The absence of a failure is not evidence of a
change: an agent then reports success in good faith, and the next session
inherits a system that is not in the state the transcript claims.

Verify by reading the state back afterwards, not by checking the exit code of
the command that was supposed to change it.
