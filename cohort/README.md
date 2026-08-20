# agentic-ai-coop cohort config

`roster.csv` is the single source of truth for this cohort: **one row per
tracked repo**, with the participant's name and GitHub handle.

- The **repo column defines which repos get scanned** — add a row to track a
  repo, delete a row to stop.
- The **participant column names them in the report**, so the naming
  convention never has to be right.
- The **github handle column** is filled by `fill_handles.py` (below).

74 repos tracked. Shared and template repos (`s2p-materials`, `s2p-skills`,
`s2p-delivery`, `s2p-cohort-board`, `S2P_WORKINGS`, and the two templates) are
deliberately absent — they aren't anyone's individual work. Add a row for any
you'd rather track.

## Running the scan

Both surfaces read the same CSV. From a cloud session, where org-wide listing
is blocked, the repo column supplies the list:

```bash
python3 .claude/skills/participant-progress/scripts/scan.py \
  --org agentic-ai-coop \
  --repos-from cohort/roster.csv \
  --roster cohort/roster.csv \
  --md /tmp/cohort.md --html /tmp/cohort.html
```

Locally, where `gh` can enumerate the org, you can let it discover repos
instead — useful for catching new repos the CSV doesn't know about yet:

```bash
python3 .claude/skills/participant-progress/scripts/scan.py \
  --org agentic-ai-coop \
  --roster cohort/roster.csv \
  --exclude '^(s2p-materials|s2p-skills|s2p-delivery|s2p-cohort-board|S2P_WORKINGS|.*-template)$' \
  --md /tmp/cohort.md --html /tmp/cohort.html
```

Anything the org run finds that isn't in the CSV still appears in the report,
under "Name inferred from repo" — that's your prompt to add a row.

## Filling in GitHub handles

Handles ship blank because they can only be read from the repos themselves.
Run this **from your own terminal**, where `gh auth login` can see the org — a
cloud session cannot reach repos it hasn't attached:

```bash
python3 .claude/skills/participant-progress/scripts/fill_handles.py \
  --org agentic-ai-coop \
  --csv cohort/roster.csv \
  --facilitator mnyamande
```

The scripts run on Python 3.6 and newer with no packages to install. If your
`python3` is older — some Anaconda installs still ship 3.6 or earlier — use the
system one instead: `/usr/bin/python3 …`.

It reads each repo's contributors, drops bot accounts (`claude`,
`github-actions`, anything ending `[bot]`), and writes the most prolific human
author into the handle column. `--facilitator mnyamande` deprioritises your own
account on repos where you made the initial commit, so the participant wins.

Only blank handles are filled, so re-running is safe and won't overwrite a
correction. Add `--dry-run` to preview. Repos it can't resolve, or where two
authors tie, are listed at the end and left blank rather than guessed at.

Then commit the filled CSV.

## Naming notes

The convention (`s2p-firstname-lastname`) produced the right name for most
rows. These were corrected by hand and should stay that way:

- Afrikaans and Dutch surname particles: `van der Merwe`, `van den Berg`,
  `van Niekerk`, `van Wyk`, `von Gordon`, `van Huyssteen`.
- `stp-pj-smit` → PJ Smit (the `stp-` prefix is fine, it just isn't `s2p-`).
- `s2p-nemita-mithal1` → Nemita Mithal.
- `s2p-Blesing_Wk2` → Blessing Ntiwane.
- `s2p-MyWordsmith` → Abayomi Aina.
