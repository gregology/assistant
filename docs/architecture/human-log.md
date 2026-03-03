# Human-Readable Audit Log

GaaS writes a daily markdown log of everything it does. This is the Principle of Audibility in practice: you can open a file and see a plain-text timeline of every action the system took on your behalf.

## How it works

The logging system (`app/human_log.py`) adds a custom `log.human()` method to Python's standard logger. It sits at level 25, between INFO (20) and WARNING (30). When you call `log.human("some message")`, two things happen: the message goes to the normal log output like any other log line, and it also gets appended to a daily markdown file.

Log files live at `logs/YYYY-MM-DD DayOfWeek.md`. Each entry is a timestamped bullet point:

```
 - 09:15 email <msg-id-1234> no longer in inbox -- moved to synced/
 - 09:15 Discovered PR **anthropic/gaas#42** -- Add GitHub integration
 - 09:16 Archived email from **noreply@example.com** -- `Your weekly digest` (uid 54321)
 - 09:18 Classified PR **anthropic/gaas#42**
```

The file uses `O_APPEND` mode, which means multiple worker processes can write to it concurrently without interleaving. POSIX guarantees atomic appends up to PIPE_BUF (4096 bytes), well above the length of any single log line.

## What gets logged

The human log captures state-changing events. Not every function call or debug trace, just the things you'd want to know about if you were reviewing what GaaS did while you weren't looking.

**Discovery events.** When GaaS finds a new email or PR for the first time:
```
 - 14:32 Discovered PR **anthropic/gaas#42** -- Add GitHub integration
```

**Classification events.** When the LLM finishes assessing something:
```
 - 14:35 Classified PR **anthropic/gaas#42**
```

**State transitions.** When tracked items leave active tracking, either because the user handled them manually or because the system acted:
```
 - 15:10 PR **anthropic/gaas#42** no longer requires attention -- moved to synced/
 - 09:15 email <msg-id-1234> no longer in inbox -- moved to synced/
```

**Action execution.** When an automation triggers an actual action on an external system:
```
 - 10:02 Archived email from **boss@company.com** -- `Q1 budget review` (uid 54321)
```

**Service results.** When a service handler finishes and its output gets saved to a note:
```
 - 14:25 Web research: research example.com terms of service changes -> services/gemini/web_research/2026_03_03__14_25_32__a1b2c3d4.md
```

The message comes from the service's `human_log` template, declared in the integration manifest or overridden per-automation in config. If no template exists, you get the generic fallback: `service.gemini.web_research: result saved (2,431 chars) -> path/to/note.md`.

**Safety warnings.** At server startup, any config issues or safety warnings are logged:
```
 - 08:00 !yolo override on automation #3 for integration "personal"
```

## When to use `log.human()` vs `log.info()`

`log.human()` is for actions and events that a person reviewing the daily summary would care about. It answers the question: "what did GaaS do today?"

`log.info()` is for operational details. Connection established, task dequeued, file written. Useful for debugging but not useful for a human reviewing the day's activity.

A good rule of thumb: if the event changes something in the outside world (moved an email, created a draft, classified a PR) or represents GaaS discovering something new, use `log.human()`. If it's internal bookkeeping, use `log.info()`.

## The daily summary as a feature

The log files are not just for debugging. They're designed to be a daily summary you can actually read. Open `logs/2026-02-23 Monday.md` and you can see at a glance how many emails were archived, which PRs were classified, and whether anything unexpected happened.

Because the format is markdown, these files also work well with note-taking tools. You could symlink the `logs/` directory into an Obsidian vault and have a searchable archive of everything GaaS has ever done.

## Implementation details

The handler is registered globally at import time. Both `app/main.py` and `app/worker.py` import `app.human_log` to ensure the handler is active in both processes. A filter ensures only `HUMAN`-level messages hit the file handler, so `log.info()` calls don't clutter the daily log.

Timestamps use local time via `datetime.now().astimezone()`, so the log reads naturally for wherever the server is running.
