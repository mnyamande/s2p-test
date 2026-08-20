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
`--loc-mode` (see below), `--include-archived`, `--no-gh` (force the REST
backend), `--verbose`.

### Lines of code

`--loc-mode estimate` (default) derives line counts from the file sizes already
in the repo tree — no extra API calls, and typically within ~5% of the real
number. The report prefixes these with `~`.

`--loc-mode exact` fetches every code blob and counts real lines. Accurate, but
it costs one API call per code file, so reserve it for a final read or a small
cohort. `--loc-mode off` drops the number entirely.

Either way only code counts: docs, lockfiles, minified bundles and vendored
directories (`node_modules/`, `dist/`, `.venv/`, …) are excluded.

### Running from a Claude Code cloud session

Org-wide listing (`/orgs/<org>/repos`) is blocked inside cloud sessions — the
session proxy only permits repo-scoped paths for the repos attached to that
session. The scan detects this and says so. Work around it with an explicit
list:

```bash
python3 scan.py --org agentic-ai-coop --repos-from cohort-repos.txt --md /tmp/cohort.md
```

`--repos-from` accepts either a roster CSV — its repo column becomes the list,
so one file drives both which repos are scanned and what the participants are
called — or a plain text file with one `owner/name` per line. Running from your
own terminal, where `gh` is unrestricted, needs none of this.

For the agentic-ai-coop cohort both files are already committed; see
`cohort/README.md` for the exact commands.

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

## 4. What the pills mean

Each participant carries up to three pills next to their name:

- **Status** — On track / Watch / Needs intervention.
- **README** — `HAS README` (green) or `NO README` (red).
- **Depth of thinking** — `DEEP` (green), `MEDIUM` (blue), `LIGHT` (grey). A
  multi-factor read of how considered the work is: README substance, CLAUDE.md
  and its revisions, tests, supporting docs, CI, module structure, and whether
  commit messages explain reasoning. It never changes a participant's status —
  it labels the character of the work, not whether they are stuck.

`LIGHT` on its own is not a problem, and is expected early in a program. It
matters when it stays light while commit volume climbs — that is someone
generating code without building understanding around it, which is worth a
conversation even though nothing here is a struggle signal.

## 5. Filling in GitHub handles

Handles cannot be read from the roster alone — they come from each repo's
commit authors. `scripts/fill_handles.py` resolves them and writes them back
into the CSV:

```bash
python3 scripts/fill_handles.py --org agentic-ai-coop \
  --csv ../../../cohort/roster.csv --facilitator mnyamande
```

It skips bot accounts, deprioritises the facilitator's own handle where another
human authored the repo, fills only blank cells (so re-runs never clobber a
manual fix), and leaves anything ambiguous blank with a note rather than
guessing. It must run somewhere the org is reachable — a cloud session cannot
read repos it has not attached.

## 6. Tuning

Thresholds live in one dict, `THRESHOLDS` at the top of `scripts/analyze.py`
(see `reference/signals.md` for what each one means and why it is set where it
is). After changing any of them, re-run the fixture test:

```bash
python3 scripts/selftest.py
```

It builds a synthetic cohort covering every signal, runs the real pipeline over
it, and asserts the statuses, evidence, ordering, and run-over-run diffing all
come out right. It needs no network and no credentials.

## 7. Snapshots

Each run writes `data/<org>/latest.json` plus a timestamped copy in
`data/<org>/runs/`. The next run diffs against `latest.json` to report change
("no new commits since the last scan", "CI was already failing last time")
rather than just a static picture. Snapshots are gitignored — they are local
state, and they contain participant activity data.

## 8. Guardrails

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
| Org listing refused (HTTP 403) | You're in a cloud session; use `--repos-from`, or run locally. See above |
| Everyone shows as "Name inferred from repo" | The `--pattern` doesn't match; check actual repo names and pass the right one or a roster |
| Scan is slow on a large org | Raise `--jobs`, lower `--max-commit-details` |
| Rate limited | The client backs off and retries automatically; lower `--jobs` if it persists |
