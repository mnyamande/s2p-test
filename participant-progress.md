# Participant Progress Skill

An on-demand Claude Code skill for facilitators running hands-on training
programs. It scans a GitHub org's participant repos and produces a
facilitator-facing briefing: who's progressing well, who's stuck, and what
specifically to look at before intervening.

Invoke it by asking Claude Code for a cohort progress report, who's gone quiet,
or a repo triage before office hours — it's registered as the
`participant-progress` skill in `.claude/skills/participant-progress/`.

## What it does

1. Lists every non-archived, non-fork repo in a GitHub org (read-only — every
   call is a GET; nothing is ever written to a participant's repo).
2. Pulls per-repo signals over a time window (default: since the last run for
   that org, else the last 14 days).
3. Snapshots the results to disk so the next run reports *change*
   ("no new commits since the last scan") rather than a static picture.
4. Writes the briefing as Markdown and as a self-contained HTML page, ordered
   by urgency and sized to read in under a minute.

## Quick start

```bash
cd .claude/skills/participant-progress/scripts

python3 scan.py --org agentic-ai-coop \
  --roster ../roster.csv \
  --md /tmp/cohort.md --html /tmp/cohort.html
```

With no `--md`/`--html`/`--out`, the Markdown briefing prints to stdout.
`python3 scan.py --help` lists every flag.

Lines of code are estimated from file sizes by default (free, within ~5%);
`--loc-mode exact` counts real lines at one API call per code file.

**Auth:** prefers the `gh` CLI when it's installed and `gh auth status` passes,
so there's no token to manage. Falls back to `GH_TOKEN` / `GITHUB_TOKEN` when
`gh` isn't available. Needs read access to the org.

## Running from a Claude Code cloud session

Cloud sessions can only reach repo-scoped API paths for the repos attached to
the session, so org-wide listing is refused there. The scan detects this and
explains it. Pass an explicit list instead:

```bash
python3 scan.py --org agentic-ai-coop --repos-from cohort-repos.txt --md /tmp/cohort.md
```

One `owner/name` (or bare repo name) per line, `#` comments allowed. Running
from your own terminal needs none of this.

## Repo → participant mapping

In priority order:

1. **Roster file** (`--roster`, CSV or JSON) — for repos that break the
   convention, and for mapping handles to real names. See
   `.claude/skills/participant-progress/roster.example.csv`.
2. **Naming convention** (`--pattern`, default `s2p-{participant}`) —
   `s2p-jane-smith` becomes "Jane Smith".
3. **Fallback** — the repo name is used as the label, and the repo is listed
   under "Name inferred from repo" in the report so the roster can be fixed.
   Nothing is silently dropped.

## Signals collected

- **Momentum** — commit count and cadence, trend vs. the previous window of
  equal length, days since last commit.
- **Working style** — commit size distribution (iterative commits vs. one big
  dump), placeholder-level commit messages.
- **Struggle markers** — consecutive fix/revert commit runs, CI failures
  persisting across commits, a long silence followed by a burst of commits.
- **Claude Code usage (bonus)** — whether `CLAUDE.md` exists and keeps getting
  revised, whether `.claude/` artifacts are committed. Never required, and
  never counted against a participant when absent.
- **Structural basics** — README, tests, tracked file count, repo size.
- **Scale and depth** — lines of code (code only; docs, lockfiles, minified
  bundles and vendored directories excluded), plus a depth-of-thinking read
  built from README substance, `CLAUDE.md` upkeep, tests, docs, CI, module
  structure and commit-message quality.

Every threshold, and the reasoning behind it, is documented in
`.claude/skills/participant-progress/reference/signals.md` and lives in one
`THRESHOLDS` dict in `scripts/analyze.py`.

## Report format

Ordered most-urgent-first, grouped by status:

1. **Status** — On track / Watch / Needs intervention, alongside a
   `HAS README` / `NO README` pill and a `DEEP` / `MEDIUM` / `LIGHT`
   depth-of-thinking pill. Neither pill changes the status — they describe the
   work, not whether someone is stuck.
2. **Evidence** — 2–4 concrete bullets ("No commits in 11 days — prior 14-day
   window had 18 commits"), not a restated score. On-track participants get a
   single line.
3. **Angle** — one actionable line, only for Watch and Needs intervention.

The footer records repos scanned, API calls made, the snapshot path, and a
standing reminder that participants should be told their repos are monitored
for progress support. The tool notifies nobody itself — that's on the
facilitator.

## Testing

```bash
python3 .claude/skills/participant-progress/scripts/selftest.py
```

Builds a synthetic nine-participant cohort covering every signal, runs the real
pipeline over it, and asserts the statuses, evidence wording, urgency ordering,
and run-over-run diffing all come out right. No network, no credentials. Run it
after changing any threshold.

## Layout

| Path | Purpose |
| --- | --- |
| `SKILL.md` | How Claude runs the scan and presents the briefing |
| `scripts/scan.py` | CLI: mapping, windows, snapshots, output |
| `scripts/ghclient.py` | Read-only GitHub client (`gh` CLI or REST) |
| `scripts/collect.py` | Per-repo signal collection |
| `scripts/analyze.py` | Thresholds, statuses, evidence, angles |
| `scripts/render.py` | Markdown and HTML renderers |
| `scripts/selftest.py` | Offline fixture test |
| `reference/signals.md` | What each signal means and why the threshold is set there |
| `data/<org>/` | Snapshots (gitignored — local participant activity data) |

## Scope

v1 is on-demand only: no scheduling, no Slack or email, no dashboard, and no
write access to participant repos. Automation comes later, once the signal
design is trusted.
