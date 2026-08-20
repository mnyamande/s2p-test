---
name: participant-progress
description: Scan a GitHub org's participant repos and produce a facilitator briefing on who is progressing, who is stuck, and what to look at before intervening. Use when the facilitator asks for a cohort progress report, wants to know who needs help or who has gone quiet, wants to triage participant repos before office hours or a session, or asks how a training cohort is doing.
---

# Participant progress briefing

On-demand triage for a hands-on training cohort. Scans every participant repo
in a GitHub org, ranks participants by how much they need attention, and gives
concrete evidence for each call — so the facilitator can act without opening
the repos first.

**Read-only.** Every API call is a GET. Nothing is written to a participant's
repo, and nobody is notified.

## 1. Confirm the inputs before scanning

Defaults for this program:

| Input | Default | When to ask |
| --- | --- | --- |
| Org | `agentic-ai-coop` | If the facilitator names a different org |
| Repo → participant | `s2p-{participant}`, e.g. `s2p-jane-smith` → "Jane Smith" | If repos don't match this shape |
| Exceptions | roster CSV (`--roster`) | Ask for the CSV path if any repo names are irregular |
| Window | since the last run, else 14 days | If they ask for a specific range |

**Do not guess the mapping.** A wrong mapping makes the whole report useless.
If repo names don't fit the convention and no roster is supplied, run the scan
anyway (unmatched repos are analysed under a name inferred from the repo name
and listed in a "Name inferred from repo" section), then tell the facilitator
which repos need a roster row.

## 2. Run the scan

```bash
cd .claude/skills/participant-progress/scripts

python3 scan.py --org agentic-ai-coop \
  --roster ../roster.csv \
  --md /tmp/cohort.md --html /tmp/cohort.html --out /tmp/cohort.json
```

Common variations:

```bash
# First run / explicit window
python3 scan.py --org agentic-ai-coop --days 14 --md /tmp/cohort.md

# Specific date range
python3 scan.py --org agentic-ai-coop --since 2026-08-01 --until 2026-08-15 ...

# Quick check on a few repos
python3 scan.py --repos agentic-ai-coop/s2p-jane-smith,agentic-ai-coop/s2p-bob-lee --no-pattern

# Dry run without touching the snapshot
python3 scan.py --org agentic-ai-coop --max-repos 3 --no-snapshot
```

`scan.py --help` lists everything. Useful flags: `--jobs` (parallel fetches,
default 6), `--max-commit-details` (per-repo cap on commit-size lookups),
`--include-archived`, `--no-gh` (force the REST backend), `--verbose`.

Auth: the script uses `gh` if it is installed and `gh auth status` passes,
otherwise `GH_TOKEN` / `GITHUB_TOKEN`. It needs read access to the org.

## 3. Present the briefing in-session

Print the Markdown report to the facilitator directly — that is the
deliverable. Then:

- Lead with the headline count: *"3 need intervention, 2 to watch, 9 on track."*
- Do not restate every participant; the report is already ordered by urgency.
- Point out anything the report flags as **changed since the last run** — that
  is the part a static read of the repos would miss.
- Mention the HTML file path if one was written (useful to keep or print).
- If any repos landed in "Name inferred from repo", say so and offer to add
  them to the roster CSV.

You may add judgement the script cannot have — e.g. "Jane's stall lines up
with the week she flagged travel." Do not soften or drop a flagged signal; the
evidence bullets are the point.

## 4. Tuning

Thresholds live in one dict, `THRESHOLDS` at the top of `scripts/analyze.py`
(see `reference/signals.md` for what each one means and why it is set where it
is). After changing any of them, re-run the fixture test:

```bash
python3 scripts/selftest.py
```

It builds a synthetic cohort covering every signal, runs the real pipeline over
it, and asserts the statuses, evidence, ordering, and run-over-run diffing all
come out right. It needs no network and no credentials.

## 5. Snapshots

Each run writes `data/<org>/latest.json` plus a timestamped copy in
`data/<org>/runs/`. The next run diffs against `latest.json` to report change
("no new commits since the last scan", "CI was already failing last time")
rather than just a static picture. Snapshots are gitignored — they are local
state, and they contain participant activity data.

## 6. Guardrails

- Read-only, always. Never add a write call to these scripts.
- **Tell the cohort their repos are monitored for progress support.** The
  report carries this reminder in its footer; the tool notifies nobody itself.
- This is coaching signal, not evaluation. Commit counts measure activity, not
  ability — surface the evidence and let the facilitator judge.
- Absent signals are not failures. No `CLAUDE.md` or no committed `.claude/`
  directory is never counted against a participant.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `No usable GitHub credentials` | `gh auth login`, or export `GH_TOKEN` |
| `Could not list repos for '<org>'` | Check the org name and that your account can see it; try `--repos owner/name` to test one repo |
| Everyone shows as "Name inferred from repo" | The `--pattern` doesn't match; check actual repo names and pass the right one or a roster |
| Scan is slow on a large org | Raise `--jobs`, lower `--max-commit-details` |
| Rate limited | The client backs off and retries automatically; lower `--jobs` if it persists |
