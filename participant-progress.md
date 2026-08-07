# Participant Progress Skill

An on-demand Claude Code skill for facilitators running hands-on training
programs. It scans a GitHub org's participant repos and produces a
facilitator-facing briefing: who's progressing well, who's stuck, and what
specifically to look at before intervening.

Invoke it by asking Claude Code to check on participants, get a cohort
progress report, or triage repos before office hours — it's registered as
the `participant-progress` skill (`~/.claude/skills/participant-progress/`).

## What it does

1. Lists every non-archived repo in a given GitHub org via the `gh` CLI
   (read-only — GET requests only, never writes to a participant's repo).
2. Pulls per-repo signals over a time window (default: since the last run
   for that org, or the last 14 days on a first run).
3. Snapshots the results to disk so the next run can report *change*
   (accelerating / flat / declining / stalled) instead of just a static
   state.
4. Prints one Markdown briefing, ordered by urgency, sized to be read in
   under a minute.

## Signals collected per repo

- **Momentum** — commit count and daily cadence over the window, trend vs.
  the previous window, days since last commit.
- **Working style** — commit size distribution (iterative small commits vs.
  one big dump), trivial/low-effort commit messages.
- **Struggle markers** — consecutive fix/revert-style commits, failing CI
  runs and whether failures persist across pushes, force-pushes that follow
  a multi-day silence.
- **Claude Code usage (bonus signal)** — presence and update frequency of
  `CLAUDE.md`, presence of a committed `.claude/` directory. Never required,
  never counted against a participant if absent.
- **Structural basics** — README, tests, file count, repo size.

## Report format

One section per participant, most-urgent first:

1. **Status line** — On track / Watch / Needs intervention.
2. **Evidence** — 2-4 concrete bullets pulled from the signals above (e.g.
   "No commits in 9 days — prior window averaged ~1/day"), not a restated
   score. Participants who are On track get a single line, no bullet list.
3. **Suggested angle for intervention** — one actionable line, only for
   Watch / Needs intervention.

The report closes with a footer: repos scanned/skipped, the window used,
and a standing reminder to tell participants their repos are monitored for
progress support (the tool surfaces this reminder — it does not send any
notice itself; that's on the facilitator to communicate out of band).

## Config needed at invocation time

- **GitHub org name** (required) — the skill asks if not given, or offers
  `gh org list` to pick from.
- **Roster file** (optional) — a JSON file mapping GitHub login → display
  name, e.g. `{"jsmith42": "Jane Smith"}`. Without one, the report uses raw
  GitHub handles.
- **Window** (optional) — defaults as described above; override with a date
  range if you want something other than "since last run."

Repo → participant mapping: every repo in the org is treated as one
participant unit, identified by repo name (with a best-effort GitHub handle
guess from the most frequent commit author in the window).

## Explicitly out of scope (v1)

- No scheduling, cron, or background execution — on-demand only.
- No Slack, email, or push notifications.
- No dashboard or web UI.
- No write access to participant repos, ever.

## Under the hood

- `~/.claude/skills/participant-progress/SKILL.md` — orchestration and
  report-writing instructions for Claude.
- `~/.claude/skills/participant-progress/scripts/scan.py` — the data
  collector. Pure Python 3.6+ standard library, shells out to `gh api` /
  `gh repo list`. Prints one JSON report to stdout and writes timestamped
  snapshots plus a `latest.json` under
  `~/.claude/skills/participant-progress/data/<org>/`.

Requires the `gh` CLI installed and authenticated (`gh auth login`) with at
least `read:org` and `repo` scope.
